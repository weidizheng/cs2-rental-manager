const BRIDGE_BASE_URL = "http://127.0.0.1:8765/api/v1/browser-bridge";
const TASK_URL = `${BRIDGE_BASE_URL}/task`;
const SNAPSHOT_URL = `${BRIDGE_BASE_URL}/order-snapshot`;
const POLL_ALARM = "cs2-rental-bridge-poll";

const PLATFORM_TABS = {
  c5: {
    patterns: ["https://www.c5game.com/user/rent*"],
    label: "C5 订单页",
  },
  eco: {
    patterns: ["https://www.ecosteam.cn/html/person/rentrecordlist.html*"],
    label: "ECO 出租记录页",
  },
  igxe: {
    patterns: ["https://www.igxe.cn/lease/seller-order-list*"],
    label: "IGXE 出租方订单页",
  },
};

async function getToken() {
  const { bridgeToken = "" } = await chrome.storage.local.get("bridgeToken");
  return bridgeToken.trim();
}

async function ensurePollingAlarm() {
  // Chrome limits extension alarms to low frequency.  The desktop app keeps
  // a pending task until this local extension returns its result.
  await chrome.alarms.create(POLL_ALARM, { periodInMinutes: 0.5 });
}

function pageCapture() {
  const text = (document.body?.innerText || "").slice(0, 1_500_000);
  const challengeDetected = /正在进行安全验证|验证您不是自动程序|安全服务防护恶意自动程序|请完成安全验证/i.test(text);
  return {
    source_url: window.location.href,
    page_title: document.title || "",
    page_text: text,
    challenge_detected: challengeDetected,
    captured_at: new Date().toISOString(),
  };
}

async function findOrderTab(platform) {
  const config = PLATFORM_TABS[platform];
  if (!config) {
    return null;
  }
  const tabs = await chrome.tabs.query({ url: config.patterns });
  if (!tabs.length) {
    return null;
  }
  return tabs.sort((left, right) => Number(right.active) - Number(left.active))[0];
}

async function sendSnapshot(token, snapshot) {
  const response = await fetch(SNAPSHOT_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CS2-Rental-Token": token,
    },
    body: JSON.stringify(snapshot),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) {
    throw new Error(data.message || data.error || `bridge HTTP ${response.status}`);
  }
}

async function processTask(token, task) {
  const snapshot = {
    task_id: task.task_id,
    platform: task.platform,
    source_url: "",
    page_title: "",
    page_text: "",
    challenge_detected: false,
    capture_error: "",
    captured_at: new Date().toISOString(),
  };

  try {
    const tab = await findOrderTab(task.platform);
    if (!tab?.id) {
      const label = PLATFORM_TABS[task.platform]?.label || "订单页";
      snapshot.capture_error = `未找到已打开的${label}。请先在默认浏览器打开对应页面。`;
    } else {
      const results = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: pageCapture,
      });
      Object.assign(snapshot, results[0]?.result || {});
    }
  } catch (error) {
    snapshot.capture_error = error instanceof Error ? error.message : String(error);
  }

  await sendSnapshot(token, snapshot);
  if (snapshot.capture_error) {
    return { ok: false, message: snapshot.capture_error };
  }
  if (snapshot.challenge_detected) {
    return { ok: false, message: "页面要求安全验证；请在该浏览器标签页中手动完成。" };
  }
  return { ok: true, message: "订单页面已发送到本地程序。" };
}

async function pollBridge() {
  const token = await getToken();
  if (!token) {
    return { ok: false, message: "请先在扩展中粘贴配对令牌。" };
  }
  try {
    const response = await fetch(TASK_URL, {
      headers: { "X-CS2-Rental-Token": token },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      return { ok: false, message: data.error || `无法连接本地程序（HTTP ${response.status}）。` };
    }
    if (!data.task) {
      return { ok: true, idle: true, message: "本地程序当前没有同步请求。" };
    }
    return await processTask(token, data.task);
  } catch (error) {
    return {
      ok: false,
      message: `无法连接本地程序：${error instanceof Error ? error.message : String(error)}`,
    };
  }
}

chrome.runtime.onInstalled.addListener(() => {
  ensurePollingAlarm();
});

chrome.runtime.onStartup.addListener(() => {
  ensurePollingAlarm();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === POLL_ALARM) {
    pollBridge();
  }
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "save_token") {
    chrome.storage.local.set({ bridgeToken: String(message.token || "").trim() })
      .then(async () => {
        await ensurePollingAlarm();
        sendResponse({ ok: true, message: "配对令牌已保存。" });
      })
      .catch((error) => sendResponse({ ok: false, message: error.message }));
    return true;
  }
  if (message?.type === "poll_now") {
    pollBridge().then(sendResponse);
    return true;
  }
  return false;
});
