import React from "react";

// ═══ 统一图标系统（16px 线性，stroke=currentColor）═══
// 右栏界面 chrome 一律用本图标集，替代 emoji：尺寸/颜色/描边一致，随主题令牌变色。
// 风格与 Sidebar / ChatPage 顶栏同源（viewBox 24、stroke-width 1.8、round）。
const PATHS = {
  // 页签
  activity: <path d="M22 12h-4l-3 9L9 3l-3 9H2" />,
  sliders: <><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3" /><path d="M1 14h6M9 8h6M17 16h6" /></>,
  folder: <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />,
  terminal: <><path d="M4 17l6-5-6-5" /><path d="M12 19h8" /></>,
  // 分组
  cpu: <><rect x="4" y="4" width="16" height="16" rx="2" /><rect x="9" y="9" width="6" height="6" /><path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3" /></>,
  thermometer: <path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z" />,
  puzzle: <path d="M20 7h-2.2a1.8 1.8 0 1 1 0-3.6H20V1h-4.2A3.8 3.8 0 0 0 8 1H4v4.2A1.8 1.8 0 1 0 4 8.8V13h4.2a1.8 1.8 0 1 1 3.6 0H20v-4.2a1.8 1.8 0 0 0 0-3.6z" />,
  palette: <><circle cx="13.5" cy="6.5" r="1" /><circle cx="17.5" cy="10.5" r="1" /><circle cx="8.5" cy="7.5" r="1" /><circle cx="6.5" cy="12.5" r="1" /><path d="M12 2a10 10 0 1 0 0 20c1.1 0 2-.9 2-2 0-.5-.2-1-.5-1.3-.3-.4-.5-.8-.5-1.2 0-1.1.9-2 2-2h2.5A3.5 3.5 0 0 0 21 12 10 10 0 0 0 12 2z" /></>,
  // 指标
  layers: <><path d="M12 2 2 7l10 5 10-5-10-5z" /><path d="M2 17l10 5 10-5" /><path d="M2 12l10 5 10-5" /></>,
  grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
  database: <><ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5" /><path d="M3 12c0 1.7 4 3 9 3s9-1.3 9-3" /></>,
  yen: <><circle cx="12" cy="12" r="9" /><path d="M8 8l4 5 4-5M12 13v5M9 15h6M9 17.5h6" /></>,
  // 文件
  file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></>,
  "folder-open": <><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1" /><path d="M3 9h18l-2 9a2 2 0 0 1-2 1.6H5.5A2 2 0 0 1 3.5 18z" /></>,
  star: <path d="M12 3l2.7 5.5 6.1.9-4.4 4.3 1 6-5.4-2.9L6.6 19.7l1-6L3.2 9.4l6.1-.9L12 3z" />,
  package: <><path d="M21 8l-9-5-9 5v8l9 5 9-5V8z" /><path d="M3 8l9 5 9-5" /><path d="M12 13v8" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  // 操作
  "chevron-right": <path d="M9 6l6 6-6 6" />,
  "chevron-down": <path d="M6 9l6 6 6-6" />,
  check: <path d="M20 6L9 17l-5-5" />,
  x: <path d="M18 6L6 18M6 6l12 12" />,
  external: <><path d="M15 3h6v6" /><path d="M10 14L21 3" /><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5" /></>,
  import: <><path d="M12 3v12" /><path d="M7 10l5 5 5-5" /><path d="M5 21h14" /></>,
  copy: <><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></>,
  rotate: <><path d="M3 12a9 9 0 1 0 2.6-6.4L3 8" /><path d="M3 3v5h5" /></>,
  eraser: <><path d="M7 21h12" /><path d="M5 13l6-6 6 6-4.5 4.5H9.5z" /></>,
  "arrow-down": <><path d="M12 5v14M6 13l6 6 6-6" /></>,
  search: <><circle cx="11" cy="11" r="7" /><path d="M21 21l-4-4" /></>,
  refresh: <><path d="M21 12a9 9 0 1 1-2.6-6.4L21 8" /><path d="M21 3v5h-5" /></>,
  play: <path d="M6 4l14 8-14 8z" />,
  stop: <rect x="6" y="6" width="12" height="12" rx="2" />,
  lock: <><rect x="4" y="10" width="16" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" /></>,
  save: <><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" /><path d="M7 3v6h8V3" /><path d="M7 21v-6h10v6" /></>,
  warning: <><path d="M12 3l10 18H2z" /><path d="M12 10v4M12 17.5h.01" /></>,
  dot: <circle cx="12" cy="12" r="4" />,
};

export function Icon({ name, size = 16, className, style, fill = "none" }) {
  const p = PATHS[name];
  if (!p) return null;
  return (
    <svg
      className={className}
      style={style}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={fill}
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {p}
    </svg>
  );
}

export default Icon;
