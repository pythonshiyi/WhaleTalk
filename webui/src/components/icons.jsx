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
  // 全站导航 / 页签 / 分区标题
  brain: <><path d="M9 3a3 3 0 0 0-3 3 3 3 0 0 0-1.6 5.6A3 3 0 0 0 6 17a3 3 0 0 0 3 3z" /><path d="M15 3a3 3 0 0 1 3 3 3 3 0 0 1 1.6 5.6A3 3 0 0 1 18 17a3 3 0 0 1-3 3z" /><path d="M12 3v17" /></>,
  shield: <path d="M12 3l8 3v6c0 4.6-3.2 8.2-8 9-4.8-.8-8-4.4-8-9V6z" />,
  "shield-check": <><path d="M12 3l8 3v6c0 4.6-3.2 8.2-8 9-4.8-.8-8-4.4-8-9V6z" /><path d="M9 12l2 2 4-4" /></>,
  key: <><circle cx="8" cy="15" r="4" /><path d="M10.8 12.2L21 2M17 6l2 2M14 9l2 2" /></>,
  server: <><rect x="3" y="3" width="18" height="7" rx="2" /><rect x="3" y="14" width="18" height="7" rx="2" /><path d="M7 6.5h.01M7 17.5h.01" /></>,
  book: <><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z" /><path d="M6 3v16" /></>,
  sparkles: <><path d="M12 3l1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6z" /><path d="M19 14l.7 2.1L22 17l-2.3.9L19 20l-.7-2.1L16 17l2.3-.9z" /></>,
  "git-branch": <><circle cx="6" cy="6" r="2.5" /><circle cx="6" cy="18" r="2.5" /><circle cx="18" cy="8" r="2.5" /><path d="M6 8.5v7M18 10.5c0 3-2 4.5-6 4.5H6" /></>,
  list: <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />,
  more: <><circle cx="5" cy="12" r="1.7" /><circle cx="12" cy="12" r="1.7" /><circle cx="19" cy="12" r="1.7" /></>,
  "trending-up": <><path d="M3 17l6-6 4 4 8-8" /><path d="M21 7v5h-5" /></>,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
  volume: <><path d="M11 5L6 9H2v6h4l5 4z" /><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" /></>,
  mic: <><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></>,
  pin: <><path d="M12 17v5" /><path d="M9 3h6l-1 7 3 3v2H7v-2l3-3z" /></>,
  pencil: <><path d="M4 20h4L20 8l-4-4L4 16z" /><path d="M14 6l4 4" /></>,
  quote: <path d="M9 7H5v6h4l-2 4M19 7h-4v6h4l-2 4" />,
  image: <><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="9" cy="9" r="2" /><path d="M21 15l-5-5L5 21" /></>,
  paperclip: <path d="M21.4 11.05l-9.19 9.19a5 5 0 01-7.07-7.07l9.19-9.19a3.5 3.5 0 014.95 4.95l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />,
  code: <path d="M8 6l-6 6 6 6M16 6l6 6-6 6" />,
  link: <><path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1" /><path d="M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1" /></>,
  download: <path d="M12 3v12M7 10l5 5 5-5M5 21h14" />,
  upload: <path d="M12 21V9M7 14l5-5 5 5M5 3h14" />,
  eye: <><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" /><circle cx="12" cy="12" r="3" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  trash: <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />,
  archive: <><rect x="3" y="3" width="18" height="5" rx="1" /><path d="M5 8v11a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8M10 12h4" /></>,
  calendar: <><rect x="3" y="4" width="18" height="18" rx="2" /><path d="M3 10h18M8 2v4M16 2v4" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>,
  help: <><circle cx="12" cy="12" r="9" /><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17h.01" /></>,
  mail: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 7l9 6 9-6" /></>,
  send: <><path d="M22 2L11 13" /><path d="M22 2l-7 20-4-9-9-4z" /></>,
  chart: <><path d="M3 3v18h18" /><path d="M7 15l3-3 3 3 4-6" /></>,
  table: <><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M3 9h18M3 15h18M9 3v18M15 3v18" /></>,
  filter: <path d="M3 5h18l-7 8v6l-4-2v-4z" />,
  message: <path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />,
  command: <path d="M9 6a3 3 0 1 0-3 3h9a3 3 0 1 0-3-3v9a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z" />,
  zap: <path d="M13 2L3 14h9l-1 8 10-12h-9z" />,
  layout: <><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M3 9h18M9 21V9" /></>,
  "book-open": <><path d="M12 6C9 4 5 4 3 5v14c2-1 6-1 9 1 3-2 7-2 9-1V5c-2-1-6-1-9 1z" /><path d="M12 6v14" /></>,
  bell: <><path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.7 21a2 2 0 0 1-3.4 0" /></>,
  globe: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c2.5 2.7 2.5 15.3 0 18M12 3c-2.5 2.7-2.5 15.3 0 18" /></>,
  downloadCloud: <><path d="M6 18a4 4 0 0 1 0-8 6 6 0 0 1 11.3-1.5A4 4 0 0 1 18 18" /><path d="M12 12v6M9 15l3 3 3-3" /></>,
  // 大脑分区 / 意识状态
  moon: <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />,
  power: <><path d="M12 3v9" /><path d="M6.4 6.4a8 8 0 1 0 11.2 0" /></>,
  target: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1.5" /></>,
  share: <><circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" /><path d="M8.6 13.5l6.8 4M15.4 6.5l-6.8 4" /></>,
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

export function hasIcon(name) {
  return typeof name === "string" && Object.prototype.hasOwnProperty.call(PATHS, name);
}

export default Icon;
