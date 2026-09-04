"""memory_store —— B1/B2 记忆统一读取层（增强批次二）。

背景：项目现有三套记忆存储、schema 各异：
  ① memory.json       对话事实（deepseek_client / stores，{facts:[{key,value,type,entities,relations,ts}]}）
  ② memory.jsonl      大脑记忆（brainkit，brain/memories/memory.jsonl，rich schema）
  ③ knowledge_index.json  文档库索引（tool_brain knowledge_*，{root, docs:[{path,text,mtime_ns}]}）

B1 目标：不重写任一活存储的落盘格式（避免数据丢失/破坏既有调用方），
而是提供「统一只读适配层」——把各源归一为同一 canonical 条目 schema
{id, text, type, importance, tags, entities, relations, source, ts, sensitivity}，
对外呈现一个 MemoryStore。

B2 目标：基于 mtime 失效的内存缓存 + 倒排索引，让跨源检索不再每次都全量
读盘解析；命中回写 hit_count 走 brainkit（F3）。

调用约定：本模块被 api_server / brain_api / 前端记忆 API 等消费；它惰性
引用 brainkit 与真实文件路径，避免与 api_server 的启动注入顺序耦合。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

# 运行时注入（由 api_server._init_dc_paths 设置后赋值；未设置时函数级兜底探测）
MEMORY_JSON_PATH = None      # DATA_DIR/memory.json
KNOWLEDGE_INDEX_PATH = None  # DATA_DIR/knowledge_index.json
BRAIN_DIR = None             # 大脑目录（默认 D:\jingyu\WhaleTalk\brain）

_INDEX_LOCK = threading.Lock()
_cache = {"mtime": None, "entries": []}  # B2 统一缓存（memory.json + brain jsonl）

_CANONICAL_FIELDS = ("id", "text", "type", "importance", "tags", "entities",
                     "relations", "source", "ts", "sensitivity", "archived",
                     "hit_count", "last_hit", "supersedes", "version_id")


def configure(memory_json=None, knowledge_index=None, brain_dir=None):
    """由宿主在启动时调用一次，注入真实路径。缺省则自动探测。"""
    global MEMORY_JSON_PATH, KNOWLEDGE_INDEX_PATH, BRAIN_DIR
    if memory_json is not None:
        MEMORY_JSON_PATH = memory_json
    if knowledge_index is not None:
        KNOWLEDGE_INDEX_PATH = knowledge_index
    if brain_dir is not None:
        BRAIN_DIR = brain_dir


def _auto_paths():
    """未显式 configure 时的兜底探测（便于单测/独立使用）。"""
    global MEMORY_JSON_PATH, BRAIN_DIR
    if MEMORY_JSON_PATH is None:
        home = os.path.expanduser("~")
        guess = os.path.join(home, "Documents", "WhaleTalk", "memory.json")
        MEMORY_JSON_PATH = guess if os.path.exists(guess) else guess
    if BRAIN_DIR is None:
        # 相对本项目源码：本项目根 /brain
        here = Path(__file__).resolve().parent
        if (here / "brain" / "manifest.json").exists():
            BRAIN_DIR = here / "brain"
    return MEMORY_JSON_PATH, KNOWLEDGE_INDEX_PATH, BRAIN_DIR


def _load_memory_json():
    """读 memory.json → facts 列表（兼容 {key,value}/{text} 两种）。"""
    p, _, _ = _auto_paths()
    out = []
    if not p or not os.path.exists(p):
        return out
    try:
        with open(p, encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
    except Exception:
        return out
    for f in (data.get("facts") or []) if isinstance(data, dict) else []:
        if not isinstance(f, dict):
            continue
        text = str(f.get("value") or f.get("text") or "").strip()
        if not text:
            continue
        key = str(f.get("key") or "").strip()
        out.append({
            "id": str(f.get("id") or f"mem:{abs(hash(text)):x}"),
            "text": text[:2000],
            "type": str(f.get("type") or key or "事实")[:20],
            "importance": int(f.get("importance") or (4 if f.get("type") in ("偏好", "规则", "联系") else 3)),
            "tags": [str(t).strip()[:20] for t in (f.get("tags") or []) if isinstance(t, str)][:10] or ([key] if key else []),
            "entities": [str(e).strip()[:30] for e in (f.get("entities") or []) if isinstance(e, str)][:20],
            "relations": [r for r in (f.get("relations") or []) if isinstance(r, dict)][:20],
            "source": "memory.json",
            "ts": str(f.get("ts") or f.get("time") or ""),
            "sensitivity": str(f.get("sensitivity") or "public"),
            "archived": False, "hit_count": 0, "last_hit": "",
            "supersedes": "", "version_id": "",
        })
    return out


def _load_brain_memories():
    """读大脑 memory.jsonl（惰性 import brainkit；未初始化返回空）。

    若 BRAIN_DIR 已配置且与当前 brainkit 指向不同，则临时切换并恢复，
    避免改变调用方的大脑上下文。
    """
    try:
        import brainkit as bk
    except Exception:
        return []
    prev = None
    switched = False
    if BRAIN_DIR is not None and str(bk.BRAIN_DIR.resolve()) != str(Path(BRAIN_DIR).resolve()):
        prev = bk.BRAIN_DIR
        try:
            bk.set_brain_dir(Path(BRAIN_DIR))
            switched = True
        except Exception:
            return []
    try:
        bk.load_manifest()
        return bk.load_memories()
    except Exception:
        return []
    finally:
        if switched and prev is not None:
            bk.set_brain_dir(prev)


def unified_entries(include_archived=False, use_cache=True):
    """B1：把 memory.json 与大脑 memory.jsonl 归一为 canonical 条目列表。

    B2：use_cache=True 时按 memory.json 的 mtime 做失效缓存，避免重复读盘。
    """
    p, _, _ = _auto_paths()
    mtime = None
    if p and os.path.exists(p):
        try:
            mtime = os.stat(p).st_mtime_ns
        except OSError:
            mtime = None
    if use_cache:
        with _INDEX_LOCK:
            if _cache.get("mtime") == mtime:
                cached = _cache.get("entries") or []
                return [e for e in cached if include_archived or not e.get("archived")]
    entries = _load_memory_json() + _load_brain_memories()
    # 去重（memory.json 与大脑可能同步过同一文本）
    seen_text, dedup = set(), []
    for e in entries:
        key = str(e.get("text") or "").strip()
        if not key or key in seen_text:
            continue
        seen_text.add(key)
        dedup.append(e)
    if use_cache:
        with _INDEX_LOCK:
            _cache["mtime"] = mtime
            _cache["entries"] = dedup
    return [e for e in dedup if include_archived or not e.get("archived")]


def invalidate_cache():
    with _INDEX_LOCK:
        _cache["mtime"] = None
        _cache["entries"] = []


# ---- 统一分词与检索（复用 brainkit 的 IDF+中文bigram 思路，独立实现避免耦合）----
def _tokens(text: str):
    import re
    s = str(text or "").lower()
    toks = re.findall(r"[a-z0-9]+", s) + re.findall(r"[\u4e00-\u9fff]", s)
    out = list(toks)
    for i in range(len(toks) - 1):
        out.append(toks[i] + toks[i + 1])
    return out


def search(query: str, limit=8, include_archived=False, sources=None):
    """跨 memory.json + 大脑 jsonl 的统一语义检索（B1/B2）。

    sources ∈ {memory.json, brain} 过滤来源；返回 canonical 条目（按相关度）。
    """
    query = str(query or "").strip()
    if not query:
        return unified_entries(include_archived=include_archived)[:limit]
    items = unified_entries(include_archived=include_archived)
    if sources:
        items = [e for e in items if (e.get("source") in sources)]
    import math
    q = _tokens(query)
    corpus = [_tokens((e.get("text") or "") + " " + " ".join(str(x) for x in (e.get("entities") or []))
              + " " + " ".join(str(t) for t in (e.get("tags") or []))) for e in items]
    n = max(1, len(items))
    idf = {}
    for t in set(q):
        df = sum(1 for c in corpus if t in c)
        idf[t] = math.log((n + 1) / (df + 1)) + 1.0
    scored = []
    for e, c in zip(items, corpus):
        common = set(q) & set(c)
        if not common:
            continue
        w = sum(idf.get(t, 1.0) for t in common)
        cos = w / (len(set(q)) ** 0.5 * len(set(c)) ** 0.5)
        recall = len(common) / len(set(q))
        scored.append((0.55 * cos + 0.45 * recall, e))
    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[:limit]]


def knowledge_docs(keyword=None, limit=5):
    """读取 knowledge_index.json 文档库（B1 的第三来源，仅文本文件索引）。"""
    p = KNOWLEDGE_INDEX_PATH
    docs = []
    if p and os.path.exists(p):
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
            for d in (data.get("docs") or []):
                if isinstance(d, dict) and d.get("path"):
                    docs.append({"id": d["path"], "text": (d.get("text") or "")[:50000],
                                 "type": "文档", "importance": 2, "tags": [],
                                 "entities": [], "relations": [], "source": "knowledge",
                                 "ts": "", "sensitivity": "public", "archived": False,
                                 "hit_count": 0, "last_hit": "", "supersedes": "", "version_id": ""})
        except Exception:
            return []
    kw = str(keyword or "").strip().lower()
    if kw:
        docs = [d for d in docs if kw in d["text"].lower() or kw in d["id"].lower()]
    return docs[:limit]


def search_all(query: str, limit=8):
    """三源统一检索：memory.json + brain jsonl（语义）+ knowledge 文档（子串）。"""
    hits = search(query, limit=limit)
    kd = knowledge_docs(query, limit=2)
    return hits + kd
