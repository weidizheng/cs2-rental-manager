const tokenInput = document.getElementById("token");
const saveButton = document.getElementById("save");
const syncButton = document.getElementById("sync");
const status = document.getElementById("status");

function setStatus(message, isError = false) {
  status.textContent = message;
  status.style.color = isError ? "#b91c1c" : "#374151";
}

chrome.storage.local.get("bridgeToken").then(({ bridgeToken = "" }) => {
  tokenInput.value = bridgeToken;
});

saveButton.addEventListener("click", async () => {
  const result = await chrome.runtime.sendMessage({
    type: "save_token",
    token: tokenInput.value,
  });
  setStatus(result.message || "令牌已保存。", !result.ok);
});

syncButton.addEventListener("click", async () => {
  setStatus("正在检查本地程序请求…");
  const result = await chrome.runtime.sendMessage({ type: "poll_now" });
  setStatus(result.message || "完成。", !result.ok);
});
