"use strict";
/* (?) hover help: one shared popover, positioned fixed so a clipped container never cuts it off. */

const helpPop = document.createElement("div");
helpPop.className = "help-pop";
helpPop.hidden = true;

function helpIcon(text) {
  const icon = document.createElement("span");
  icon.className = "help";
  icon.textContent = "?";
  icon.tabIndex = 0;
  icon.setAttribute("role", "note");
  icon.setAttribute("aria-label", text);
  const show = () => {
    if (!helpPop.isConnected) document.body.appendChild(helpPop);
    helpPop.textContent = text;
    helpPop.hidden = false;
    const r = icon.getBoundingClientRect();
    const w = helpPop.offsetWidth, h = helpPop.offsetHeight;
    helpPop.style.left = Math.min(Math.max(8, r.left - 8), window.innerWidth - w - 8) + "px";
    helpPop.style.top = (r.bottom + 6 + h > window.innerHeight - 8 ? r.top - h - 6 : r.bottom + 6) + "px";
  };
  const hide = () => { helpPop.hidden = true; };
  icon.addEventListener("mouseenter", show);
  icon.addEventListener("mouseleave", hide);
  icon.addEventListener("focus", show);
  icon.addEventListener("blur", hide);
  icon.addEventListener("click", e => e.stopPropagation());
  return icon;
}
