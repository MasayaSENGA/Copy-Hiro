// 同じフレームに二重に読み込まれたとき（読み込み中のページへの再注入が重なった場合など）は何もしない
(() => {
  if (globalThis.__copyHiroLoaded) return;
  globalThis.__copyHiroLoaded = true;

  // PDF ビューアーでは右クリックした位置を取得できないので何もしない
  if (document.contentType === "application/pdf") return;

  let lastElement = null;

  // ページ側が document より手前で伝播を止めても記録できるよう、window のキャプチャで受け取る
  window.addEventListener("contextmenu", (event) => {
    // シャドウDOMの内側・外側どちらの表にも対応するため、イベントの経路上でいちばん内側の表を記録する
    const path = event.composedPath();
    lastElement = path.find(node => node instanceof HTMLTableElement) || path[0] || event.target;
  }, true);

  // セル内の改行・タブは列ずれの原因になるので空白に置き換える
  const cellText = (cell) => cell.innerText.trim().replace(/[\t\r\n]+/g, " ");

  // テーブル要素をタブ区切りテキストに変換する共通関数
  const tableToText = (table) => {
    return Array.from(table.rows).map(row => {
      return Array.from(row.cells)
        .map(cellText)
        .join("\t");
    }).join("\n");
  };

  // テーブル要素をMarkdownテーブルに変換する共通関数
  const tableToMarkdown = (table) => {
    const rows = Array.from(table.rows).map(row =>
      Array.from(row.cells).map(cell =>
        cellText(cell).replace(/\|/g, "\\|")
      )
    );
    if (rows.length === 0) return "";

    // 行数が非常に多い表でもスタックが溢れないよう reduce で求める
    const colCount = rows.reduce((max, row) => Math.max(max, row.length), 0);
    const pad = (row) => {
      const padded = row.slice();
      while (padded.length < colCount) padded.push("");
      return padded;
    };

    const [header, ...body] = rows.map(pad);
    const headerLine = `| ${header.join(" | ")} |`;
    const separatorLine = `| ${Array(colCount).fill("---").join(" | ")} |`;
    const bodyLines = body.map(row => `| ${row.join(" | ")} |`);

    return [headerLine, separatorLine, ...bodyLines].join("\n");
  };

  // 右クリックした位置のテーブルを探す（見つからなければトーストで知らせる）
  const findClickedTable = () => {
    if (!lastElement) {
      // 拡張機能のインストール・更新前から開いていたページでは、右クリックした位置が記録されていない
      showToast("もう一度テーブルを右クリックしてからお試しください。");
      return null;
    }
    const table = lastElement.closest ? lastElement.closest("table") : null;
    if (!table) showToast("テーブルが見つかりませんでした。");
    return table;
  };

  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "copy_table") {
      const table = findClickedTable();
      if (!table) return;
      copyToClipboard(tableToText(table), "テーブルをコピーしました！");

    } else if (request.action === "copy_table_markdown") {
      const table = findClickedTable();
      if (!table) return;
      copyToClipboard(tableToMarkdown(table), "テーブルをMarkdownでコピーしました！");

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

    } else if (request.action === "copy_all_tables_markdown") {
      const allTables = document.querySelectorAll("table");
      if (allTables.length === 0) {
        showToast("テーブルが見つかりませんでした。");
        return;
      }
      const allContent = Array.from(allTables)
        .map(table => tableToMarkdown(table))
        .join("\n\n");

      copyToClipboard(allContent, `${allTables.length} 個のテーブルをMarkdownでコピーしました！`);

    } else if (request.action === "notify_reload") {
      // 右クリックしたフレームに注入できなかったとき（更新前から開いていたタブの別オリジンのiframeなど）
      showToast("ページを再読み込みしてからお試しください。");
    }
  });

  // クリップボードコピーと通知の実行
  function copyToClipboard(text, successMessage) {
    writeClipboard(text).then(() => {
      showToast(successMessage);
    }).catch(err => {
      console.error("コピー失敗:", err);
      showToast("コピーに失敗しました。");
    });
  }

  async function writeClipboard(text) {
    // http:// のページなど navigator.clipboard が使えない場合は execCommand で代替する
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return;
      } catch (err) {
        // 下の execCommand で再試行する
      }
    }
    if (!execCopy(text)) throw new Error("execCommand('copy') に失敗しました");
  }

  // copy イベントで書き込む内容を差し替える（clipboardWrite 権限によりユーザー操作なしでも動作する）
  function execCopy(text) {
    let copied = false;
    const onCopy = (event) => {
      event.clipboardData.setData("text/plain", text);
      event.preventDefault();
      event.stopImmediatePropagation();
      copied = true;
    };
    document.addEventListener("copy", onCopy, true);
    try {
      document.execCommand("copy");
    } finally {
      document.removeEventListener("copy", onCopy, true);
    }
    return copied;
  }

  // 画面右上にカスタム通知（トースト）を表示する
  function showToast(message) {
    // フレームセットやSVGなど、HTMLのbodyが無い文書では表示しない
    if (!(document.body instanceof HTMLBodyElement)) return;

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
})();