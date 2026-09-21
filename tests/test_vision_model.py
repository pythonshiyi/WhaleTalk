"""视觉能力判定：同一多模态模型在不同供应商下的 id 应一致判为「支持图片」。

背景：`deepseek-v4.1-flash`（第三方网关对统一多模态模型的命名）此前因名字不含
"vision" 被误判为不支持图片 → 发送带图消息直接报「不支持图片输入」而中断。
"""
import deepseek_client as dc


def test_official_and_legacy_names_are_vision():
    assert dc.is_vision_model("deepseek-flash") is True
    assert dc.is_vision_model("deepseek-v4-flash-vision-exp") is True
    assert dc.is_vision_model("deepseek-v4.1-flash-expires-on-0910") is True


def test_provider_specific_deepseek_flash_ids_are_vision():
    # 同一模型在不同供应商的 id（OpenCode Go 等）——必须判为多模态
    assert dc.is_vision_model("deepseek-v4.1-flash") is True
    assert dc.is_vision_model("deepseek-v4-flash") is True
    assert dc.is_vision_model("DeepSeek-V4.1-Flash") is True


def test_other_multimodal_families():
    assert dc.is_vision_model("gpt-4o") is True
    assert dc.is_vision_model("claude-3-5-sonnet") is True
    assert dc.is_vision_model("gemini-1.5-pro") is True
    assert dc.is_vision_model("qwen2.5-vl-7b") is True
    assert dc.is_vision_model("glm-4v") is True


def test_text_only_models_are_not_vision():
    assert dc.is_vision_model("") is False
    assert dc.is_vision_model("llama-3-8b-instruct") is False
    assert dc.is_vision_model("deepseek-chat") is False
    assert dc.is_vision_model("deepseek-reasoner") is False
