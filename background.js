const MENU_ITEMS = [
  // 単一テーブル用
  { id: "copyTable", title: "このテーブルをコピー", action: "copy_table" },
  { id: "copyTableMarkdown", title: "このテーブルをMarkdownでコピー", action: "copy_table_markdown" },
  // 全テーブル用
  { id: "copyAllTables", title: "ページ上の全テーブルをコピー", action: "copy_all_tables" },
  { id: "copyAllTablesMarkdown", title: "ページ上の全テーブルをMarkdownでコピー", action: "copy_all_tables_markdown" }
];

chrome.runtime.onInstalled.addListener(() => {
  // 更新時などに同じIDで二重登録しないよう、いったん全て消してから登録する
  chrome.contextMenus.removeAll(() => {
    for (const item of MENU_ITEMS) {
      chrome.contextMenus.create({
        id: item.id,
        title: item.title,
        contexts: ["all"]
      });
    }
  });
});

// content.js が読み込まれていないタブに送信したときのエラー
const NO_RECEIVER = /Receiving end does not exist|Could not establish connection/;

async function handleMenuClick(info, tab) {
  const item = MENU_ITEMS.find(menuItem => menuItem.id === info.menuItemId);
  if (!item || !tab || typeof tab.id !== "number" || tab.id < 0) return;

  // 右クリックしたフレームにだけ送る（iframe内の表にも対応）
  const frameId = typeof info.frameId === "number" ? info.frameId : 0;
  if (await deliver(tab.id, frameId, { action: item.action })) return;

  // 更新前から開いていたタブの別オリジンのiframeなど、右クリックしたフレームに注入できなかったときは
  // トップのフレームで再読み込みを案内する（再読み込みすれば content.js が自動で読み込まれる）
  if (frameId !== 0 && /^https?:/.test(info.frameUrl || "")) {
    await deliver(tab.id, 0, { action: "notify_reload" });
  }
}

// フレームにメッセージを送る。content.js が無ければ注入してから送り直す（届いたら true）
async function deliver(tabId, frameId, message) {
  try {
    await chrome.tabs.sendMessage(tabId, message, { frameId });
    return true;
  } catch (err) {
    if (!NO_RECEIVER.test(String(err && err.message))) {
      console.warn("コピひろ:", err);
      return true;
    }
  }

  // 拡張機能のインストール・更新前から開いていたタブには content.js が無いので、注入してから送り直す
  try {
    await chrome.scripting.executeScript({
      target: { tabId, frameIds: [frameId] },
      files: ["content.js"]
    });
    await chrome.tabs.sendMessage(tabId, message, { frameId });
    return true;
  } catch (err) {
    // chrome:// のページやChromeウェブストアなど、拡張機能が動作できないページでは何もしない
    return false;
  }
}

chrome.contextMenus.onClicked.addListener(handleMenuClick);