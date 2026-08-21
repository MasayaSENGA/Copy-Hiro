chrome.runtime.onInstalled.addListener(() => {
  // 単一テーブル用
  chrome.contextMenus.create({
    id: "copyTable",
    title: "このテーブルをコピー",
    contexts: ["all"]
  });

  chrome.contextMenus.create({
    id: "copyTableMarkdown",
    title: "このテーブルをMarkdownでコピー",
    contexts: ["all"]
  });

  // 全テーブル用
  chrome.contextMenus.create({
    id: "copyAllTables",
    title: "ページ上の全テーブルをコピー",
    contexts: ["all"]
  });

  chrome.contextMenus.create({
    id: "copyAllTablesMarkdown",
    title: "ページ上の全テーブルをMarkdownでコピー",
    contexts: ["all"]
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "copyTable") {
    chrome.tabs.sendMessage(tab.id, { action: "copy_table" });
  } else if (info.menuItemId === "copyTableMarkdown") {
    chrome.tabs.sendMessage(tab.id, { action: "copy_table_markdown" });
  } else if (info.menuItemId === "copyAllTables") {
    chrome.tabs.sendMessage(tab.id, { action: "copy_all_tables" });
  } else if (info.menuItemId === "copyAllTablesMarkdown") {
    chrome.tabs.sendMessage(tab.id, { action: "copy_all_tables_markdown" });
  }
});