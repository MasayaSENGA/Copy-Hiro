let lastElement = null;

document.addEventListener("contextmenu", (event) => {
  lastElement = event.target;
}, true);

// テーブル要素をタブ区切りテキストに変換する共通関数
const tableToText = (table) => {
  return Array.from(table.rows).map(row => {
    return Array.from(row.cells)
      .map(cell => cell.innerText.trim().replace(/\n/g, " "))
      .join("\t");
  }).join("\n");
};

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "copy_table") {
    const table = lastElement ? lastElement.closest("table") : null;
    if (!table) {
      showToast("テーブルが見つかりませんでした。");
      return;
    }
    copyToClipboard(tableToText(table), "テーブルをコピーしました！");

  } else if (request.action === "copy_all_tables") {
    const allTables = document.querySelectorAll("table");
    if (allTables.length === 0) {
      showToast("テーブルが見つかりませんでした。");
      return;
    }
    const allContent = Array.from(allTables)
      .map(table => tableToText(table))
      .join("\n\n");

    copyToClipboard(allContent, `${allTables.length} 個のテーブルをコピーしました！`);
  }
});

// クリップボードコピーと通知の実行
function copyToClipboard(text, successMessage) {
  navigator.clipboard.writeText(text).then(() => {
    showToast(successMessage);
  }).catch(err => {
    console.error("コピー失敗:", err);
    showToast("コピーに失敗しました。");
  });
}

// 画面右上にカスタム通知（トースト）を表示する
function showToast(message) {
  // 既存のトーストがあれば削除
  const oldToast = document.getElementById("table-copier-toast");
  if (oldToast) oldToast.remove();

  const toast = document.createElement("div");
  toast.id = "table-copier-toast";
  toast.innerText = message;
  
  // スタイル設定
  Object.assign(toast.style, {
    position: "fixed",
    top: "20px",
    right: "20px",
    backgroundColor: "#333",
    color: "#fff",
    padding: "12px 20px",
    borderRadius: "8px",
    fontSize: "14px",
    zIndex: "999999",
    boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
    transition: "opacity 0.3s, transform 0.3s",
    opacity: "0",
    transform: "translateY(-10px)",
    pointerEvents: "none",
    fontFamily: "sans-serif"
  });

  document.body.appendChild(toast);

  // 表示アニメーション
  setTimeout(() => {
    toast.style.opacity = "1";
    toast.style.transform = "translateY(0)";
  }, 10);

  // 3秒後に消去
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(-10px)";
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}