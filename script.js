// 导航栏滚动效果
const nav = document.getElementById("nav");
const onScroll = () => {
  if (window.scrollY > 20) nav.classList.add("scrolled");
  else nav.classList.remove("scrolled");
};
window.addEventListener("scroll", onScroll, { passive: true });
onScroll();

// 滚动进入视口时的揭示动画
const io = new IntersectionObserver(
  (entries) => {
    entries.forEach((e) => {
      if (e.isIntersecting) {
        e.target.classList.add("in");
        io.unobserve(e.target);
      }
    });
  },
  { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
);
document.querySelectorAll(".reveal").forEach((el, i) => {
  el.style.transitionDelay = `${(i % 3) * 0.08}s`;
  io.observe(el);
});

// 鼠标跟随光晕
const glow = document.getElementById("cursorGlow");
let raf = null;
window.addEventListener("mousemove", (e) => {
  if (raf) return;
  raf = requestAnimationFrame(() => {
    glow.style.left = e.clientX + "px";
    glow.style.top = e.clientY + "px";
    glow.style.opacity = "1";
    raf = null;
  });
});
window.addEventListener("mouseleave", () => (glow.style.opacity = "0"));

/* =========================================================
   待替换的占位链接 —— 准备好后改这两个变量即可：
   1. DOWNLOAD_URL：你的 .dmg 下载直链（建议 GitHub Releases）
   2. GUIDE_URL：你的使用教程页面（Notion / 文档站等）
   ========================================================= */
const DOWNLOAD_URL = "https://thebridge13.oss-cn-guangzhou.aliyuncs.com/the-bridge/The-Bridge.dmg"; // 稳定直链，每次发版覆盖更新
const GUIDE_URL = "https://tssoft.atlassian.net/wiki/x/dQDfg";

const dlBtn = document.getElementById("downloadBtn");
const guideLink = document.getElementById("guideLink");
if (DOWNLOAD_URL !== "#" && dlBtn) dlBtn.href = DOWNLOAD_URL;
if (GUIDE_URL !== "#" && guideLink) guideLink.href = GUIDE_URL;

// 占位链接点击提示
document.querySelectorAll('a[href="#"]').forEach((a) => {
  a.addEventListener("click", (e) => {
    e.preventDefault();
    alert("链接待替换：请在 script.js 中填入真实的下载 / 教程地址。");
  });
});
