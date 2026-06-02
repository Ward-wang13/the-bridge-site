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
   下载 / 教程链接：按部署环境自适应
   - 内网站（TAE 镜像，已内置 /resources/）：直接下载 DMG / 打开教程
   - 公开站（thebridge.top / GitHub Pages，无 DMG）：提示在内网获取
   注意：不在公开源码中硬编码内网地址，避免泄露内部基础设施。
   ========================================================= */
const PUBLIC_HOSTS = ["thebridge.top", "www.thebridge.top", "ward-wang13.github.io"];
const isPublicSite = PUBLIC_HOSTS.includes(location.hostname);

const dlBtn = document.getElementById("downloadBtn");
const guideLink = document.getElementById("guideLink");

// 使用教程：统一指向 Confluence 文档（新标签打开），内外网一致
const GUIDE_URL = "https://tssoft.atlassian.net/wiki/x/dQDfg";
if (guideLink) {
  guideLink.href = GUIDE_URL;
  guideLink.target = "_blank";
  guideLink.rel = "noopener noreferrer";
}

// 安装包下载：内网站直接下载镜像内的 DMG；公开站提示到内网获取
if (isPublicSite) {
  if (dlBtn) {
    dlBtn.addEventListener("click", (e) => {
      e.preventDefault();
      alert("The Bridge 是公司内部工具，安装包请在公司内网的内部门户中获取。");
    });
  }
} else {
  if (dlBtn) dlBtn.href = "/resources/The-Bridge.dmg";
}
