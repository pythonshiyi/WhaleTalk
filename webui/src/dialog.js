// 应用内对话框（替代原生 window.confirm / window.prompt）。
// 好处：可样式化、非阻塞、与整套设计一致、支持键盘（Esc 取消 / Enter 确认）。
//
// 用法：
//   import { confirmDialog, promptDialog } from "../dialog.js";
//   if (!(await confirmDialog("删除该会话？"))) return;
//   const ans = await promptDialog("请输入名称", "默认值");
//
// 实现：模块级单例 handler，由 <DialogHost/> 在挂载时注入。
// 宿主缺失（如单测环境）时优雅回退到原生对话框，保证不崩。

let _handler = null;

export function _setDialogHandler(h) {
  _handler = h;
}

export function confirmDialog(message, opts = {}) {
  if (_handler) return _handler({ kind: "confirm", message, ...opts });
  try {
    return Promise.resolve(window.confirm(message));
  } catch {
    return Promise.resolve(false);
  }
}

export function promptDialog(message, defaultValue = "", opts = {}) {
  if (_handler) return _handler({ kind: "prompt", message, defaultValue, ...opts });
  let v = null;
  try {
    v = window.prompt(message, defaultValue);
  } catch {
    /* ignore */
  }
  return Promise.resolve(v);
}
