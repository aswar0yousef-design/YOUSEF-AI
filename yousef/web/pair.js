"use strict";
// Pairs this phone with Yousef on the PC; the session cookie comes back HttpOnly.
document.addEventListener("DOMContentLoaded", () => {
  const $ = (s) => document.querySelector(s);
  const guess = /iphone/i.test(navigator.userAgent) ? "iPhone" : /ipad/i.test(navigator.userAgent) ? "iPad"
    : /android/i.test(navigator.userAgent) ? "جوال أندرويد" : "متصفح";
  $("#name").value = guess;
  $("#pair-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const line = $("#pair-status");
    line.classList.remove("is-error");
    line.textContent = "يتحقق…";
    try {
      const res = await fetch("/api/remote/pair", {
        method: "POST",
        headers: { "X-Yousef-Client": "1", "Content-Type": "application/json" },
        body: JSON.stringify({ code: $("#code").value.trim(), name: $("#name").value.trim() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `خطأ ${res.status}`);
      line.textContent = "تم الربط.";
      location.replace("/");
    } catch (err) {
      line.textContent = err.message === "Failed to fetch" ? "تعذّر الوصول إلى يوسف؛ تأكد أن الكمبيوتر يعمل وعلى الشبكة نفسها." : err.message;
      line.classList.add("is-error");
    }
  });
});
