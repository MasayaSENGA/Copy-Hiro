# コピひろ プライバシーポリシー

[English version](#copy-hiro-privacy-policy)

制定日：2026年10月5日

Chrome 拡張機能「コピひろ」（以下「本拡張機能」）が取り扱う情報と、その取り扱い方法について説明します。本拡張機能は、表をコピーするために必要な最小限の情報だけを、お使いの端末内で取り扱います。

## 概要

- 本拡張機能が扱うのは、ページ上の表（テーブル）のテキストと、どの表を右クリックしたかを特定するための情報だけです。
- 表のテキストを読み取るのは、右クリックメニューからコピーの項目を選んだときだけです。
- 読み取ったテキストはお使いの端末内で変換し、クリップボードに書き込むだけです。保存、外部への送信、第三者への提供は一切行いません。

## 1. 対象

本ポリシーは、Chrome ウェブストアで公開している Chrome 拡張機能「コピひろ」（Copy-Hiro）に適用されます。本拡張機能は、Chrome で表示しているウェブページと、ページに埋め込まれたフレーム（iframe など。about:blank や srcdoc で作られたフレームを含みます）の中で動作します。本拡張機能の「ファイルの URL へのアクセスを許可する」を有効にした場合は、端末内のファイルを開いたページも含みます。

Chrome の設定画面（chrome:// で始まるページ）や Chrome ウェブストア、PDF ビューアなど、Chrome が拡張機能の動作を認めていないページでは動作しません。これらのページでは、メニューの項目を選んでも何も起こりません。

## 2. 取り扱う情報

本拡張機能が取り扱う情報は、次の 3 つだけです。

### (1) 表のテキスト

右クリックメニューで選んだ項目に応じて、次の表の各セルのテキスト（文字だけ）を読み取ります。

- 「このテーブルをコピー」「このテーブルをMarkdownでコピー」：右クリックした表
- 「ページ上の全テーブルをコピー」「ページ上の全テーブルをMarkdownでコピー」：右クリックしたページにあるすべての表。ページに埋め込まれたフレームの中で右クリックした場合は、そのフレームにあるすべての表です（ページとフレームの表をまとめて読み取ることはありません）。画面に表示されていない表も含まれます。

表のテキストを読み取るのは、これらの項目を選んだときだけです。表に個人情報などが含まれている場合も、そのテキストはコピーのためだけに端末内で扱われます。

### (2) 右クリックした要素

どの表を右クリックしたかを特定するため、ページ上で右クリックが行われるたびに、右クリックした位置にある要素（その位置が表の中であれば、その表）を示す情報を、そのページ（フレーム）のメモリ上に直近の 1 件だけ保持します。この時点では、表やページのテキストは読み取りません。この情報は次に右クリックしたときに置き換わり、ページを閉じたり再読み込みしたりすると破棄されます。

### (3) メニューの項目を選んだときの情報

メニューの項目を選ぶと、Chrome から本拡張機能に、その操作に関する情報が渡されます。本拡張機能が使うのは、そのうち次の情報だけです。

- 選ばれた項目と、右クリックしたタブ・フレームを識別する番号：コピーの指示を、右クリックしたフレームに届けるため
- 右クリックしたフレームの URL が「http://」または「https://」で始まるかどうか：フレームの中で右クリックしてコピーの指示を届けられなかったときに、ページの再読み込みを案内するかどうかを決めるため（このときだけ確認します）

これらの情報は、その場で使うだけで、記録も送信もしません。

### 取り扱わない情報

上記以外の情報、たとえばページの URL（上記 (3) の確認を除きます）やタイトル、表以外のページの内容、閲覧履歴、Cookie、位置情報、アカウント情報などを、本拡張機能が利用、記録、送信することはありません。

## 3. 利用目的

- 表のテキスト：タブ区切りテキスト（TSV）または Markdown 形式に変換し、クリップボードに書き込むためだけに使用します。
- 右クリックした要素：コピーの対象となる表を特定するためだけに使用します。
- メニューの項目を選んだときの情報：コピーの指示を右クリックしたフレームに届けることと、ページの再読み込みを案内するかどうかを決めることだけに使用します。

本拡張機能によるこれらの情報の使用は、Chrome ウェブストアのユーザーデータに関するポリシー（Chrome Web Store User Data Policy）に、Limited Use（使用の制限）の要件も含めて準拠しています。

## 4. 保存・送信・第三者提供

- 処理はすべて、お使いの端末のブラウザ内で完結します。表のテキストは、右クリックしたページ（フレーム）の中で変換してクリップボードに書き込みます。本拡張機能のバックグラウンドで動く処理にも送りません。
- 本拡張機能は外部との通信を一切行いません。情報を開発者や第三者のサーバーへ送信することはありません。
- 情報を保存しません。拡張機能のストレージ、Cookie、ローカルストレージなどの保存領域は使用していません。
- 情報を第三者に提供、販売、共有することはありません。
- アクセス解析、広告、トラッキングの仕組みは組み込んでいません。
- 外部からプログラム（リモートコード）を読み込んで実行することはありません。

なお、Chrome ウェブストアは、インストール数などの集計された統計情報を、開発者向けの管理画面で提供しています。これは Google が Chrome ウェブストアの運営の中で集計するもので、本拡張機能が収集・送信するものではありません。

## 5. クリップボードについて

- コピーの項目を選ぶと、変換した表のテキストがクリップボードに書き込まれます。それまでクリップボードにあった内容は上書きされます。
- 本拡張機能がクリップボードの内容を読み取ることはありません。
- クリップボードに書き込まれた内容は、通常のコピー操作と同じく、お使いの端末のクリップボードに残ります。お使いの OS のクリップボード履歴や同期の機能を有効にしている場合は、その機能の設定に従って保存・同期されることがあります。貼り付けた後の取り扱いは、貼り付け先のアプリケーションやサービスの方針に従います。

## 6. ページの表示について

コピーの結果（コピーできなかった場合を含みます）や、操作をやり直す必要があることを知らせるため、ページの右上（フレームの中で操作した場合は、そのフレームの右上）に通知を約 3 秒間表示します。ただし、フレームの中で右クリックし、そのフレームで本拡張機能が動作できなかった場合は、ページの再読み込みを案内する通知を、ページ全体の右上に表示します。それ以外に、ページの内容を変更することはありません。

## 7. 権限について

本拡張機能が使用する権限と、その用途は次のとおりです。

| 権限 | 用途 |
| --- | --- |
| `contextMenus` | 右クリックメニューにコピーの項目（4 項目）を追加するため |
| `clipboardWrite` | 変換したテキストをクリップボードに書き込むため（読み取りには使用しません） |
| `activeTab` | 本拡張機能のインストールや更新より前から開いていたタブなど、コピー処理用のスクリプトがまだ読み込まれていないページで、メニューの項目を選んだときに、そのタブへ一時的にアクセスし、スクリプトを読み込み直すため。一時的にアクセスできるのは、タブに表示しているページと同じオリジン（プロトコル・ドメイン・ポート番号が同じ範囲）だけです。オリジンが異なるフレーム（別のサイトから埋め込まれたフレームなど）には読み込み直せないため、その場合はページの再読み込みを案内します（再読み込みすると、スクリプトは自動で読み込まれます） |
| `scripting` | 上記の読み込み直しを行うため（本拡張機能に同梱されたスクリプトだけを、必要なフレームにだけ読み込みます。同じフレームに重ねて読み込まれた場合、2 回目以降は何もしません） |
| すべてのページでのスクリプトの実行（`content_scripts`：`<all_urls>`、すべてのフレーム、about:blank のフレームを含む） | どのサイトの表でも、またページに埋め込まれたフレーム（about:blank や srcdoc で作られたフレームを含みます）の中の表でも、右クリックした表を特定できるようにするため |

すべてのページでスクリプトを実行する設定があるため、Chrome はインストール時に、すべてのウェブサイト上のデータを読み取り・変更できる旨の警告を表示します。また `clipboardWrite` のため、コピー＆ペーストするデータを変更できる旨の警告も表示します（正確な文言は Chrome のバージョンや言語によって異なります）。本拡張機能が実際に行う処理は、本ポリシーに記載した範囲に限られます。

## 8. ポリシーの改定

本ポリシーは、本拡張機能の機能変更や、関連する法令・規約の変更に応じて改定することがあります。改定した場合は、このページの内容と改定日を更新してお知らせします。取り扱う情報の範囲や利用方法を変更する場合は、その変更を含むバージョンを公開する前に、このページと Chrome ウェブストアの掲載情報でお知らせします。

## 9. お問い合わせ

本ポリシーおよび本拡張機能に関するお問い合わせは、次の連絡先までお寄せください。Chrome ウェブストアの本拡張機能のページに表示されている開発者の連絡先からもお問い合わせいただけます。

- 運営者：【要記入: パブリッシャー名（個人名または会社名）】
- 連絡先：【要記入: 公開してよい問い合わせ用メールアドレスまたはフォームのURL】

お問い合わせの際にいただいたメールアドレスなどの情報は、お問い合わせへの回答のためだけに使用します。

制定日：2026年10月5日

---

# Copy-Hiro Privacy Policy

[日本語版](#コピひろ-プライバシーポリシー)

Effective date: October 5, 2026

This policy explains what information the Chrome extension "Copy-Hiro" (コピひろ, "the extension") handles and how. The extension handles only the minimum information needed to copy tables, and only on your device.

This English version is a translation of the Japanese original. If the two versions differ, the Japanese version prevails.

## Summary

- The only data the extension handles is the text of tables on the page and the information needed to identify which table you right-clicked.
- It reads table text only when you choose a copy item from the right-click menu.
- The text is converted on your device and written to your clipboard. It is never stored, sent anywhere, or shared with anyone.

## 1. Scope

This policy applies to the Chrome extension "Copy-Hiro" (コピひろ) published on the Chrome Web Store. The extension works on web pages displayed in Chrome and inside frames embedded in them (such as iframes, including frames created as about:blank or with srcdoc). If you turn on "Allow access to file URLs" for the extension, this also includes pages that show local files on your device.

The extension does not work on pages where Chrome does not allow extensions to run, such as Chrome's settings pages (pages whose address starts with chrome://), the Chrome Web Store, and the PDF viewer. On those pages, choosing a menu item does nothing.

## 2. Information the extension handles

The extension handles only the following three kinds of information.

### (1) Table text

Depending on the menu item you choose, the extension reads the text (text only) of each cell of the following tables:

- 「このテーブルをコピー」 (Copy this table) and 「このテーブルをMarkdownでコピー」 (Copy this table as Markdown): the table you right-clicked.
- 「ページ上の全テーブルをコピー」 (Copy all tables on the page) and 「ページ上の全テーブルをMarkdownでコピー」 (Copy all tables on the page as Markdown): all tables on the page you right-clicked. If you right-clicked inside a frame embedded in the page, all tables in that frame (tables in the page and in its frames are never read together). Tables that are not visible on screen are included.

Table text is read only when you choose one of these items. Even if a table contains personal information, its text is handled on your device solely for copying.

### (2) The element you right-clicked

To identify which table you right-clicked, each time you right-click on a page the extension keeps one piece of information in the memory of that page (or frame): which element is at the position you right-clicked most recently (or, if that position is inside a table, which table). No table or page text is read at that point. This information is replaced the next time you right-click, and it is discarded when you close or reload the page.

### (3) Information about the menu item you choose

When you choose a menu item, Chrome passes information about that action to the extension. Of that information, the extension uses only the following:

- Which item you chose, and the numbers that identify the tab and frame you right-clicked: to deliver the copy instruction to the frame you right-clicked.
- Whether the URL of the frame you right-clicked starts with "http://" or "https://": to decide whether to show a notice asking you to reload the page, when you right-clicked inside a frame and the copy instruction could not be delivered to it (this is checked only in that case).

This information is used only at that moment; it is neither recorded nor transmitted.

### Information the extension does not handle

The extension does not use, record, or transmit any other information, such as page URLs (except for the check described in (3)) or titles, page content other than tables, browsing history, cookies, location, or account information.

## 3. How the information is used

- Table text: used only to convert it to tab-separated text (TSV) or Markdown and write it to the clipboard.
- The right-clicked element: used only to identify the table to copy.
- Information about the menu item you choose: used only to deliver the copy instruction to the frame you right-clicked and to decide whether to show a notice asking you to reload the page.

The extension's use of this information complies with the Chrome Web Store User Data Policy, including the Limited Use requirements.

## 4. Storage, transmission, and sharing

- All processing takes place inside the browser on your device. Table text is converted and written to the clipboard within the page (or frame) you right-clicked; it is not passed even to the extension's background process.
- The extension makes no network requests. It never sends information to the developer's servers or to any third party.
- The extension stores no information. It does not use extension storage, cookies, local storage, or any other storage.
- The extension never provides, sells, or shares information with third parties.
- The extension contains no analytics, advertising, or tracking.
- The extension never loads or runs code from outside its package (remote code).

Note that the Chrome Web Store provides developers with aggregated statistics, such as the number of installs, in its developer dashboard. These are compiled by Google as part of operating the Chrome Web Store; they are not collected or sent by the extension.

## 5. Clipboard

- When you choose a copy item, the converted table text is written to the clipboard, replacing whatever it contained before.
- The extension never reads the contents of the clipboard.
- As with any ordinary copy, the copied text stays on your device's clipboard. If you have turned on your operating system's clipboard history or sync features, the text may be saved or synced according to those settings. Once you paste it, it is handled according to the policies of the app or service you paste it into.

## 6. Changes to the page

To report the result of a copy (including when nothing could be copied) or to tell you that you need to try again, the extension shows a notice in the top-right corner of the page (or of the frame, if you used it inside a frame) for about 3 seconds. If you right-clicked inside a frame where the extension could not run, a notice asking you to reload the page is shown in the top-right corner of the whole page instead. The extension makes no other changes to the page.

## 7. Permissions

The extension uses the following permissions for the following purposes.

| Permission | Purpose |
| --- | --- |
| `contextMenus` | To add the four copy items to the right-click menu |
| `clipboardWrite` | To write the converted text to the clipboard (never used to read it) |
| `activeTab` | In tabs where the copy script is not loaded yet, such as tabs that were already open before the extension was installed or updated, to access the tab temporarily when you choose a menu item, so that the script can be loaded into it again. This temporary access covers only the same origin (the same protocol, domain, and port) as the page shown in the tab. Because the script cannot be loaded again into frames from a different origin (such as frames embedded from other sites), the extension then shows a notice asking you to reload the page (after a reload, the script is loaded automatically) |
| `scripting` | To load that script again (only the script bundled with the extension is used, and only into the frames that need it; if it is loaded into the same frame more than once, the extra copies do nothing) |
| Running a script on all pages (`content_scripts`: `<all_urls>`, all frames, including about:blank frames) | To identify the table you right-clicked on any site, including tables inside frames embedded in a page (including frames created as about:blank or with srcdoc) |

Because the extension runs a script on all pages, Chrome warns at installation that the extension can read and change your data on all websites. Because of `clipboardWrite`, Chrome also warns that the extension can modify data you copy and paste. (The exact wording depends on the Chrome version and language.) What the extension actually does is limited to what this policy describes.

## 8. Changes to this policy

This policy may be revised when the extension's features change or when relevant laws or rules change. When it is revised, this page and its revision date will be updated. If the scope or use of the information the extension handles changes, the change will be announced on this page and in the Chrome Web Store listing before the version that includes it is published.

## 9. Contact

For questions about this policy or the extension, please use the contact details below. You can also use the developer contact information shown on the extension's Chrome Web Store page.

- Operator: 【要記入: パブリッシャー名（個人名または会社名）】
- Contact: 【要記入: 公開してよい問い合わせ用メールアドレスまたはフォームのURL】

Any information you send with your inquiry, such as your email address, is used only to reply to you.

Effective date: October 5, 2026
