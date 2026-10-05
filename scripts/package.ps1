#Requires -Version 5.1
<#
.SYNOPSIS
    Chrome ウェブストアにアップロードする zip パッケージを作成します。

.DESCRIPTION
    manifest.json を検証し、拡張機能の実行に必要なファイルだけを
    dist\copy-hiro-v<version>.zip にまとめます。

    同梱するファイル:
      - manifest.json（必須）
      - background.js / content.js（存在する場合）
      - icons\*.png
      - _locales\ 以下（存在する場合）

    zip 内のパス区切りは常に "/" です。Windows PowerShell 5.1 の Compress-Archive は
    "\" 区切りのエントリを書き出すことがあり、ウェブストアに拒否されるため使いません。

.EXAMPLE
    リポジトリのルートで次のように実行します。
    powershell -ExecutionPolicy Bypass -File scripts\package.ps1

.NOTES
    Windows PowerShell 5.1 と PowerShell 7 の両方で動作します。
    日本語を含むため、このファイルは UTF-8（BOM 付き）で保存してください。
    BOM がないと Windows PowerShell 5.1 が日本語を正しく読み込めません。
#>
[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

# ---- 設定 ----------------------------------------------------------------

# zip ファイル名の接頭辞（dist\<接頭辞>-v<version>.zip）
$PackagePrefix = 'copy-hiro'

# ルート直下に置く実行時ファイル。manifest.json 以外は存在する場合のみ同梱します。
# 新しいファイル（popup.html など）を追加したときは、ここに追記してください。
$RootFiles = @('manifest.json', 'background.js', 'content.js')

# 同梱しない OS のゴミファイル
$JunkNames = @('.DS_Store', 'Thumbs.db', 'desktop.ini')

$NameMaxLength = 75
$DescriptionMaxLength = 132
$DashboardUrl = 'https://chrome.google.com/webstore/devconsole'

# ---- 出力ヘルパー --------------------------------------------------------

$script:WarningList = New-Object System.Collections.Generic.List[string]

function Write-Step {
    param([string]$Message)
    Write-Host ''
    Write-Host $Message -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host ('  [OK]   ' + $Message)
}

function Write-Info {
    param([string]$Message)
    Write-Host ('  [情報] ' + $Message) -ForegroundColor DarkGray
}

function Write-Warn {
    param([string]$Message)
    $script:WarningList.Add($Message)
    Write-Host ('  [警告] ' + $Message) -ForegroundColor Yellow
}

function Exit-WithError {
    param([string]$Message, [string[]]$Details = @())
    Write-Host ''
    Write-Host ('エラー: ' + $Message) -ForegroundColor Red
    foreach ($detail in $Details) {
        Write-Host ('  - ' + $detail) -ForegroundColor Red
    }
    Write-Host 'パッケージの作成を中止しました。' -ForegroundColor Red
    exit 1
}

# ---- JSON / ファイルのヘルパー -------------------------------------------

function Test-JsonKey {
    # manifest のキーは大文字小文字を区別するため、完全一致で存在を確認します。
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $false }
    if ($Object -isnot [System.Management.Automation.PSCustomObject]) { return $false }
    foreach ($property in $Object.PSObject.Properties) {
        if ($property.Name -ceq $Name) { return $true }
    }
    return $false
}

function Get-JsonValue {
    # 文字列・数値などのスカラー値を取得します（配列は呼び出し側で直接参照すること）。
    param($Object, [string]$Name)
    if (Test-JsonKey $Object $Name) { return $Object.$Name }
    return $null
}

function Read-JsonFile {
    # UTF-8 の JSON ファイルを読み込みます。バイト列とパース結果を返します。
    param([string]$Path, [string]$DisplayName)

    try {
        $bytes = [System.IO.File]::ReadAllBytes($Path)
    } catch {
        Exit-WithError ('{0} を読み込めません。' -f $DisplayName) @($_.Exception.Message)
    }

    if ($bytes.Length -ge 2 -and (($bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) -or ($bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF))) {
        Exit-WithError ('{0} が UTF-16 で保存されています。UTF-8 で保存し直してください。' -f $DisplayName)
    }

    $offset = 0
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        Write-Warn ('{0} に BOM が付いています。BOM なしの UTF-8 で保存することをおすすめします。' -f $DisplayName)
        $offset = 3
    }

    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    try {
        $text = $strictUtf8.GetString($bytes, $offset, $bytes.Length - $offset)
    } catch {
        Exit-WithError ('{0} を UTF-8 として読み込めません（Shift_JIS などで保存されていませんか？）。UTF-8 で保存し直してください。' -f $DisplayName)
    }

    if ($text.Trim().Length -eq 0) {
        Exit-WithError ('{0} が空です。' -f $DisplayName)
    }

    try {
        $json = ConvertFrom-Json -InputObject $text
    } catch {
        Exit-WithError ('{0} が正しい JSON ではありません。カンマや括弧、引用符の閉じ忘れがないか確認してください。' -f $DisplayName) @($_.Exception.Message)
    }

    if ($json -isnot [System.Management.Automation.PSCustomObject]) {
        Exit-WithError ('{0} の最上位が JSON オブジェクト（波括弧で囲まれた形式）になっていません。' -f $DisplayName)
    }

    return [pscustomobject]@{ Bytes = $bytes; Json = $json }
}

function Get-VersionProblem {
    # Chrome の version 形式（0～65535 の整数を 1～4 個、"." 区切り）を検証します。
    # 問題がなければ $null、あれば理由を返します。
    param([string]$Version)
    if ([string]::IsNullOrEmpty($Version)) { return '空です' }
    $parts = $Version.Split('.')
    if ($parts.Length -gt 4) { return '数字は 4 つまでです（例: 1.2.3.4）' }
    foreach ($part in $parts) {
        # \d は全角数字にも一致するため [0-9] を使います
        if ($part -notmatch '^[0-9]+\z') { return ('"{0}" は半角の整数ではありません' -f $part) }
        if ($part.Length -gt 1 -and $part.StartsWith('0')) { return ('"{0}" のように 0 から始まる数字は使えません' -f $part) }
        if ($part.Length -gt 5 -or [int]$part -gt 65535) { return ('"{0}" が 65535 を超えています' -f $part) }
    }
    return $null
}

function ConvertTo-EntryName {
    # manifest 内のパスを zip のエントリ名（"/" 区切り・先頭の "./" や "/" なし）に正規化します。
    param([string]$Path)
    $normalized = $Path.Replace('\', '/')
    while ($normalized.StartsWith('./')) { $normalized = $normalized.Substring(2) }
    return $normalized.TrimStart('/')
}

function Get-RelativeEntryName {
    param([string]$FullPath, [string]$Root)
    if (-not $FullPath.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
        Exit-WithError ('リポジトリ外のファイルです: {0}' -f $FullPath)
    }
    return $FullPath.Substring($Root.Length).TrimStart('\', '/').Replace('\', '/')
}

function Test-JunkName {
    param([string]$Name)
    if ($Name.StartsWith('.')) { return $true }
    foreach ($junk in $JunkNames) {
        if ($Name -ieq $junk) { return $true }
    }
    return $false
}

function Find-ChildItem {
    # 大文字小文字を区別せずに子要素を探し、実際の名前の項目を返します。
    param([string]$Parent, [string]$Name, [switch]$Folder)
    if ($Folder) {
        $items = @(Get-ChildItem -LiteralPath $Parent -Directory -Force)
    } else {
        $items = @(Get-ChildItem -LiteralPath $Parent -File -Force)
    }
    foreach ($item in $items) {
        if ($item.Name -ieq $Name) { return $item }
    }
    return $null
}

function Get-PngSize {
    # PNG の IHDR から幅と高さを読み取ります。PNG でなければ $null を返します。
    param([byte[]]$Bytes)
    $signature = @(0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)
    if ($Bytes.Length -lt 24) { return $null }
    for ($i = 0; $i -lt $signature.Length; $i++) {
        if ($Bytes[$i] -ne $signature[$i]) { return $null }
    }
    $width = ([int64]$Bytes[16] -shl 24) -bor ([int64]$Bytes[17] -shl 16) -bor ([int64]$Bytes[18] -shl 8) -bor [int64]$Bytes[19]
    $height = ([int64]$Bytes[20] -shl 24) -bor ([int64]$Bytes[21] -shl 16) -bor ([int64]$Bytes[22] -shl 8) -bor [int64]$Bytes[23]
    return [pscustomobject]@{ Width = $width; Height = $height }
}

function Get-Sha256Hex {
    param([byte[]]$Bytes)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-', '')
    } finally {
        $sha.Dispose()
    }
}

function Get-GitInfo {
    # 同梱ファイルに未コミットの変更がないかを確認します（git がなければ何もしません）。
    param([string]$Root, [string[]]$Paths)
    $info = [pscustomobject]@{ Available = $false; Commit = $null; Dirty = @() }
    $git = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $git) { return $info }

    # Windows PowerShell 5.1 では Stop のままだと git の標準エラー出力で例外になるため緩めます
    $ErrorActionPreference = 'Continue'
    try {
        $inside = & $git.Path -C $Root rev-parse --is-inside-work-tree 2>$null
        if ($LASTEXITCODE -ne 0 -or ("$inside").Trim() -ne 'true') { return $info }

        $commit = & $git.Path -C $Root rev-parse --short HEAD 2>$null
        if ($LASTEXITCODE -eq 0) { $info.Commit = ("$commit").Trim() }

        $status = @(& $git.Path -C $Root status --porcelain --untracked-files=all -- $Paths 2>$null)
        if ($LASTEXITCODE -eq 0) {
            $info.Available = $true
            $info.Dirty = @($status | Where-Object { ("$_").Trim() -ne '' })
        }
    } catch {
        # git の確認は参考情報なので、失敗してもパッケージ作成は続けます
    }
    return $info
}

# ---- 本体 ----------------------------------------------------------------

function Invoke-Main {
    $repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))

    Write-Host 'Chrome ウェブストア用のパッケージを作成します' -ForegroundColor White
    Write-Host ('  リポジトリ: {0}' -f $repoRoot)

    # -- 1. manifest.json の検証 ------------------------------------------
    Write-Step '[1/5] manifest.json を検証しています...'

    $manifestItem = Find-ChildItem -Parent $repoRoot -Name 'manifest.json'
    if ($null -eq $manifestItem) {
        Exit-WithError ('manifest.json が見つかりません: {0}' -f (Join-Path $repoRoot 'manifest.json'))
    }
    if ($manifestItem.Name -cne 'manifest.json') {
        Exit-WithError ('ファイル名は小文字の manifest.json にしてください（現在: {0}）。' -f $manifestItem.Name)
    }

    $manifestData = Read-JsonFile -Path $manifestItem.FullName -DisplayName 'manifest.json'
    $manifest = $manifestData.Json

    # manifest_version
    $manifestVersion = Get-JsonValue $manifest 'manifest_version'
    if ($null -eq $manifestVersion) {
        Exit-WithError 'manifest_version がありません。"manifest_version": 3 を指定してください。'
    }
    if ("$manifestVersion" -ne '3') {
        Exit-WithError ('manifest_version が {0} です。ウェブストアに公開するには 3 にしてください。' -f $manifestVersion)
    }
    Write-Ok 'manifest_version: 3'

    # version
    if (-not (Test-JsonKey $manifest 'version')) {
        Exit-WithError 'version がありません。例: "version": "1.0"'
    }
    $version = $manifest.version
    if ($version -isnot [string]) {
        Exit-WithError ('version は文字列で指定してください（例: "version": "{0}"）。' -f $version)
    }
    $versionProblem = Get-VersionProblem $version
    if ($null -ne $versionProblem) {
        Exit-WithError ('version "{0}" は Chrome のバージョン形式に合っていません: {1}。' -f $version, $versionProblem) @(
            '0～65535 の整数を "." で区切って 1～4 個並べてください（例: 1.0 / 2.3.1）。'
        )
    }
    Write-Ok ('version: {0}' -f $version)

    # _locales / default_locale（__MSG_xxx__ の解決に使います）
    $localesItem = Find-ChildItem -Parent $repoRoot -Name '_locales' -Folder
    $defaultLocale = Get-JsonValue $manifest 'default_locale'
    $defaultMessages = $null
    if ($null -ne $localesItem -and $null -eq $defaultLocale) {
        Exit-WithError '_locales フォルダがありますが、manifest.json に default_locale が指定されていません。'
    }
    if ($null -ne $defaultLocale) {
        if ($null -eq $localesItem) {
            Exit-WithError ('default_locale "{0}" が指定されていますが、_locales フォルダがありません。' -f $defaultLocale)
        }
        $messagesPath = Join-Path (Join-Path $localesItem.FullName $defaultLocale) 'messages.json'
        if (-not (Test-Path -LiteralPath $messagesPath -PathType Leaf)) {
            Exit-WithError ('既定ロケールのファイルがありません: _locales/{0}/messages.json' -f $defaultLocale)
        }
        $defaultMessages = (Read-JsonFile -Path $messagesPath -DisplayName ('_locales/{0}/messages.json' -f $defaultLocale)).Json
        Write-Ok ('default_locale: {0}' -f $defaultLocale)
    }

    # name / description（__MSG_xxx__ 形式なら既定ロケールの文言で検証します）
    $resolved = @{}
    $notes = @{}
    foreach ($field in @('name', 'description')) {
        $value = Get-JsonValue $manifest $field
        if ($null -eq $value -or $value -isnot [string] -or $value.Trim().Length -eq 0) {
            Exit-WithError ('{0} がないか、空になっています。' -f $field)
        }
        $note = ''
        if ($value -match '^__MSG_([A-Za-z0-9_@]+)__\z') {
            $messageKey = $Matches[1]
            if ($null -eq $defaultMessages) {
                Exit-WithError ('{0} が {1} を参照していますが、default_locale が指定されていません。' -f $field, $value)
            }
            $message = $null
            foreach ($property in $defaultMessages.PSObject.Properties) {
                if ($property.Name -ieq $messageKey) {
                    $message = Get-JsonValue $property.Value 'message'
                }
            }
            if ($null -eq $message -or $message -isnot [string]) {
                Exit-WithError ('{0} が参照するメッセージ "{1}" が _locales/{2}/messages.json にありません。' -f $field, $messageKey, $defaultLocale)
            }
            $value = $message
            $note = '（{0} の messages.json より）' -f $defaultLocale
        }
        $resolved[$field] = $value
        $notes[$field] = $note
    }

    $name = $resolved['name']
    if ($name.Length -gt $NameMaxLength) {
        Write-Warn ('name が {0} 文字です。ウェブストアの上限は {1} 文字です。' -f $name.Length, $NameMaxLength)
    } else {
        Write-Ok ('name: {0}{1}' -f $name, $notes['name'])
    }

    $description = $resolved['description']
    if ($description.Length -gt $DescriptionMaxLength) {
        Write-Warn ('description が {0} 文字です。上限の {1} 文字を超えているため、ウェブストアでエラーになります。' -f $description.Length, $DescriptionMaxLength)
    } else {
        Write-Ok ('description: {0} 文字（上限 {1} 文字）{2}' -f $description.Length, $DescriptionMaxLength, $notes['description'])
    }

    if (Test-JsonKey $manifest 'key') {
        Write-Warn 'manifest.json に "key" があります。ウェブストアへのアップロード時に拒否されるため、削除してください。'
    }

    # -- 2. 同梱ファイルの収集 --------------------------------------------
    Write-Step '[2/5] 同梱するファイルを集めています...'

    # エントリ名 -> バイト列（読み込みは 1 回だけ。作成中にファイルが変わっても中身がぶれないようにします）
    $entries = New-Object 'System.Collections.Generic.SortedDictionary[string,byte[]]' ([System.StringComparer]::Ordinal)
    $entries['manifest.json'] = $manifestData.Bytes

    foreach ($fileName in $RootFiles) {
        if ($fileName -ieq 'manifest.json') { continue }
        $item = Find-ChildItem -Parent $repoRoot -Name $fileName
        if ($null -eq $item) {
            Write-Info ('{0} はないため同梱しません。' -f $fileName)
            continue
        }
        $entries[$item.Name] = [System.IO.File]::ReadAllBytes($item.FullName)
    }

    $iconsItem = Find-ChildItem -Parent $repoRoot -Name 'icons' -Folder
    if ($null -ne $iconsItem) {
        $pngFiles = @(Get-ChildItem -LiteralPath $iconsItem.FullName -File -Force |
            Where-Object { $_.Extension -ieq '.png' -and -not (Test-JunkName $_.Name) })
        foreach ($png in $pngFiles) {
            $entries[(Get-RelativeEntryName $png.FullName $repoRoot)] = [System.IO.File]::ReadAllBytes($png.FullName)
        }
    }

    if ($null -ne $localesItem) {
        $localeFiles = @(Get-ChildItem -LiteralPath $localesItem.FullName -File -Recurse -Force |
            Where-Object { -not (Test-JunkName $_.Name) })
        foreach ($localeFile in $localeFiles) {
            $relative = Get-RelativeEntryName $localeFile.FullName $repoRoot
            # 途中のフォルダが "." で始まるもの（.git など）は除外します
            if ($relative -match '(^|/)\.') { continue }
            $entries[$relative] = [System.IO.File]::ReadAllBytes($localeFile.FullName)
        }
    }

    # manifest.json を先頭に、残りはエントリ名順に並べます
    $entryNames = New-Object System.Collections.Generic.List[string]
    $entryNames.Add('manifest.json')
    foreach ($key in $entries.Keys) {
        if ($key -cne 'manifest.json') { $entryNames.Add($key) }
    }
    Write-Ok ('{0} 個のファイルを同梱します。' -f $entryNames.Count)

    # -- 3. manifest が参照するファイルの確認 ------------------------------
    Write-Step '[3/5] manifest.json が参照するファイルを確認しています...'

    $references = New-Object System.Collections.Generic.List[object]
    $referenceErrors = New-Object System.Collections.Generic.List[string]

    $addReference = {
        param([string]$Field, $Value)
        if ($null -eq $Value) { return }
        if ($Value -isnot [string] -or $Value.Trim().Length -eq 0) {
            $referenceErrors.Add(('{0}: ファイルのパスが文字列になっていません。' -f $Field))
            return
        }
        $references.Add([pscustomobject]@{ Field = $Field; Path = $Value })
    }

    if (Test-JsonKey $manifest 'icons') {
        if ($manifest.icons -isnot [System.Management.Automation.PSCustomObject]) {
            Exit-WithError 'icons はサイズとパスの組（例: "128": "icons/icon128.png"）で指定してください。'
        }
        foreach ($property in $manifest.icons.PSObject.Properties) {
            & $addReference ('icons.{0}' -f $property.Name) $property.Value
        }
        if (-not (Test-JsonKey $manifest.icons '128')) {
            Write-Warn 'icons に "128" がありません。ウェブストアには 128x128 のアイコンが必要です。'
        }
    } else {
        Write-Warn 'icons がありません。ウェブストアには 128x128 のアイコンが必要です。'
    }

    if (Test-JsonKey $manifest 'background') {
        & $addReference 'background.service_worker' (Get-JsonValue $manifest.background 'service_worker')
    }

    if (Test-JsonKey $manifest 'content_scripts') {
        $index = 0
        foreach ($contentScript in @($manifest.content_scripts)) {
            foreach ($kind in @('js', 'css')) {
                if (Test-JsonKey $contentScript $kind) {
                    foreach ($scriptPath in @($contentScript.$kind)) {
                        & $addReference ('content_scripts[{0}].{1}' -f $index, $kind) $scriptPath
                    }
                }
            }
            $index++
        }
    }

    if (Test-JsonKey $manifest 'action') {
        $action = $manifest.action
        if (Test-JsonKey $action 'default_icon') {
            $defaultIcon = $action.default_icon
            if ($defaultIcon -is [string]) {
                & $addReference 'action.default_icon' $defaultIcon
            } elseif ($defaultIcon -is [System.Management.Automation.PSCustomObject]) {
                foreach ($property in $defaultIcon.PSObject.Properties) {
                    & $addReference ('action.default_icon.{0}' -f $property.Name) $property.Value
                }
            }
        }
        & $addReference 'action.default_popup' (Get-JsonValue $action 'default_popup')
    }

    & $addReference 'options_page' (Get-JsonValue $manifest 'options_page')
    if (Test-JsonKey $manifest 'options_ui') {
        & $addReference 'options_ui.page' (Get-JsonValue $manifest.options_ui 'page')
    }
    if (Test-JsonKey $manifest 'side_panel') {
        & $addReference 'side_panel.default_path' (Get-JsonValue $manifest.side_panel 'default_path')
    }
    & $addReference 'devtools_page' (Get-JsonValue $manifest 'devtools_page')

    $includedExact = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    $includedIgnoreCase = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($entryName in $entryNames) {
        [void]$includedExact.Add($entryName)
        [void]$includedIgnoreCase.Add($entryName)
    }
    $referenced = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)

    foreach ($reference in $references) {
        $entryName = ConvertTo-EntryName $reference.Path
        if ($reference.Path.Contains('\')) {
            Write-Warn ('{0}: パスの区切りには "\" ではなく "/" を使ってください（{1}）。' -f $reference.Field, $reference.Path)
        }
        if ($entryName -match '(^|/)\.\.(/|$)' -or $entryName.Contains(':')) {
            $referenceErrors.Add(('{0}: 拡張機能のフォルダの外を指しています（{1}）。' -f $reference.Field, $reference.Path))
            continue
        }
        if ($includedExact.Contains($entryName)) {
            [void]$referenced.Add($entryName)
            Write-Ok ('{0} -> {1}' -f $reference.Field, $entryName)
            continue
        }
        if ($includedIgnoreCase.Contains($entryName)) {
            $actual = $null
            foreach ($candidate in $entryNames) {
                if ($candidate -ieq $entryName) { $actual = $candidate }
            }
            $referenceErrors.Add(('{0}: "{1}" は大文字小文字が実際のファイル名 "{2}" と違います。Windows では動いても、ウェブストア版では読み込めません。' -f $reference.Field, $reference.Path, $actual))
            continue
        }
        if (Test-Path -LiteralPath (Join-Path $repoRoot $entryName) -PathType Leaf) {
            $referenceErrors.Add(('{0}: "{1}" は存在しますが同梱対象に入っていません。scripts\package.ps1 の $RootFiles に追加してください。' -f $reference.Field, $entryName))
            continue
        }
        $referenceErrors.Add(('{0}: "{1}" が見つかりません。' -f $reference.Field, $entryName))
    }

    if ($referenceErrors.Count -gt 0) {
        Exit-WithError 'manifest.json が参照するファイルに問題があります。' $referenceErrors.ToArray()
    }

    # アイコンの実寸が manifest の指定と合っているか確認します
    if (Test-JsonKey $manifest 'icons') {
        foreach ($property in $manifest.icons.PSObject.Properties) {
            if ($property.Name -notmatch '^[0-9]+\z') { continue }
            $entryName = ConvertTo-EntryName $property.Value
            if (-not $entries.ContainsKey($entryName)) { continue }
            $size = Get-PngSize $entries[$entryName]
            if ($null -eq $size) {
                Write-Warn ('{0} は PNG 形式ではありません。' -f $entryName)
            } elseif ($size.Width -ne [int64]$property.Name -or $size.Height -ne [int64]$property.Name) {
                Write-Warn ('{0} は {1}x{2} ですが、manifest.json では {3}x{3} として指定されています。' -f $entryName, $size.Width, $size.Height, $property.Name)
            }
        }
    }

    foreach ($entryName in $entryNames) {
        if ($entryName.StartsWith('icons/') -and -not $referenced.Contains($entryName)) {
            Write-Info ('{0} は manifest.json から参照されていませんが同梱します。' -f $entryName)
        }
    }

    $gitInfo = Get-GitInfo -Root $repoRoot -Paths $entryNames.ToArray()
    if ($gitInfo.Available -and @($gitInfo.Dirty).Count -gt 0) {
        Write-Warn 'コミットされていない変更があるファイルを同梱します。公開する内容で間違いないか確認してください。'
        foreach ($line in $gitInfo.Dirty) {
            Write-Host ('           {0}' -f $line) -ForegroundColor Yellow
        }
    }

    # -- 4. zip の作成 ----------------------------------------------------
    Write-Step '[4/5] zip を作成しています...'

    Add-Type -AssemblyName System.IO.Compression

    $distDir = Join-Path $repoRoot 'dist'
    [void][System.IO.Directory]::CreateDirectory($distDir)
    $zipFileName = '{0}-v{1}.zip' -f $PackagePrefix, $version
    $zipPath = Join-Path $distDir $zipFileName
    $tempPath = $zipPath + '.tmp'

    $expectedHashes = @{}
    $fileStream = $null
    $archive = $null
    try {
        $fileStream = New-Object System.IO.FileStream($tempPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        $archive = New-Object System.IO.Compression.ZipArchive($fileStream, [System.IO.Compression.ZipArchiveMode]::Create, $false)
        foreach ($entryName in $entryNames) {
            $bytes = $entries[$entryName]
            $expectedHashes[$entryName] = Get-Sha256Hex $bytes

            $sourcePath = Join-Path $repoRoot ($entryName.Replace('/', '\'))
            $lastWrite = [System.IO.File]::GetLastWriteTime($sourcePath)
            if ($lastWrite.Year -lt 1980 -or $lastWrite.Year -gt 2107) { $lastWrite = Get-Date }

            $entry = $archive.CreateEntry($entryName, [System.IO.Compression.CompressionLevel]::Optimal)
            $entry.LastWriteTime = New-Object System.DateTimeOffset($lastWrite)
            $entryStream = $entry.Open()
            try {
                $entryStream.Write($bytes, 0, $bytes.Length)
            } finally {
                $entryStream.Dispose()
            }
        }
    } catch {
        $message = $_.Exception.Message
        if ($null -ne $archive) { try { $archive.Dispose() } catch { } ; $archive = $null }
        if ($null -ne $fileStream) { $fileStream.Dispose(); $fileStream = $null }
        if (Test-Path -LiteralPath $tempPath) { Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue }
        Exit-WithError 'zip の作成に失敗しました。' @($message)
    } finally {
        if ($null -ne $archive) { $archive.Dispose() }
        if ($null -ne $fileStream) { $fileStream.Dispose() }
    }

    try {
        if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
        Move-Item -LiteralPath $tempPath -Destination $zipPath
    } catch {
        $message = $_.Exception.Message
        Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
        Exit-WithError ('{0} を上書きできません。ほかのアプリで開いていないか確認してください。' -f $zipPath) @($message)
    }
    Write-Ok ('dist\{0} を書き出しました。' -f $zipFileName)

    # -- 5. 作成した zip の検証 -------------------------------------------
    Write-Step '[5/5] 作成した zip を検証しています...'

    $zipProblems = New-Object System.Collections.Generic.List[string]
    $listing = New-Object System.Collections.Generic.List[object]
    $readStream = $null
    $readArchive = $null
    try {
        $readStream = [System.IO.File]::OpenRead($zipPath)
        $readArchive = New-Object System.IO.Compression.ZipArchive($readStream, [System.IO.Compression.ZipArchiveMode]::Read, $false)
        $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
        foreach ($zipEntry in $readArchive.Entries) {
            $entryName = $zipEntry.FullName
            if ($entryName.Contains('\')) { $zipProblems.Add(('"\" を含むエントリ名があります: {0}' -f $entryName)) }
            if ($entryName.EndsWith('/')) { $zipProblems.Add(('フォルダだけのエントリがあります: {0}' -f $entryName)) }
            if (-not $expectedHashes.ContainsKey($entryName)) {
                $zipProblems.Add(('想定外のエントリがあります: {0}' -f $entryName))
                continue
            }
            [void]$seen.Add($entryName)

            $entryStream = $zipEntry.Open()
            $memory = New-Object System.IO.MemoryStream
            try {
                $entryStream.CopyTo($memory)
            } finally {
                $entryStream.Dispose()
            }
            $content = $memory.ToArray()
            $memory.Dispose()
            if ((Get-Sha256Hex $content) -ne $expectedHashes[$entryName]) {
                $zipProblems.Add(('中身が元のファイルと一致しません: {0}' -f $entryName))
            }
            $listing.Add([pscustomobject]@{ Name = $entryName; Length = $zipEntry.Length; Compressed = $zipEntry.CompressedLength })
        }
        foreach ($entryName in $entryNames) {
            if (-not $seen.Contains($entryName)) { $zipProblems.Add(('zip に入っていません: {0}' -f $entryName)) }
        }
        if (-not $seen.Contains('manifest.json')) { $zipProblems.Add('zip のルートに manifest.json がありません。') }
    } catch {
        $zipProblems.Add($_.Exception.Message)
    } finally {
        if ($null -ne $readArchive) { $readArchive.Dispose() }
        if ($null -ne $readStream) { $readStream.Dispose() }
    }

    if ($zipProblems.Count -gt 0) {
        Exit-WithError ('作成した zip に問題があります: {0}' -f $zipPath) $zipProblems.ToArray()
    }
    Write-Ok 'エントリ名はすべて "/" 区切りで、manifest.json はルートにあります。'
    Write-Ok '各ファイルの中身が元のファイルと一致しました。'

    # -- 結果 -------------------------------------------------------------
    $zipLength = (Get-Item -LiteralPath $zipPath).Length
    $zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash

    Write-Host ''
    Write-Host ('収録ファイル（{0} 件）:' -f $listing.Count) -ForegroundColor White
    foreach ($row in $listing) {
        Write-Host ('  {0,-28} {1,8:N0} バイト（圧縮後 {2:N0} バイト）' -f $row.Name, $row.Length, $row.Compressed)
    }

    Write-Host ''
    Write-Host 'パッケージを作成しました。' -ForegroundColor Green
    Write-Host ('  ファイル : {0}' -f $zipPath)
    Write-Host ('  サイズ   : {0:N1} KB（{1:N0} バイト）' -f ($zipLength / 1024), $zipLength)
    Write-Host ('  SHA-256  : {0}' -f $zipHash)
    if ($null -ne $gitInfo.Commit) {
        $commitNote = ''
        if (@($gitInfo.Dirty).Count -gt 0) { $commitNote = '（未コミットの変更を含む）' }
        Write-Host ('  コミット : {0}{1}' -f $gitInfo.Commit, $commitNote)
    }

    if ($script:WarningList.Count -gt 0) {
        Write-Host ''
        Write-Host ('警告が {0} 件あります。アップロード前に内容を確認してください。' -f $script:WarningList.Count) -ForegroundColor Yellow
        foreach ($warning in $script:WarningList) {
            Write-Host ('  - {0}' -f $warning) -ForegroundColor Yellow
        }
    }

    Write-Host ''
    Write-Host '次のステップ:' -ForegroundColor White
    Write-Host '  1. Chrome ウェブストアのデベロッパー ダッシュボードを開きます。'
    Write-Host ('     {0}' -f $DashboardUrl)
    Write-Host ('  2. 初めて公開するときは「新しいアイテム」から、更新するときは対象アイテムの「パッケージ」から')
    Write-Host ('     dist\{0} をアップロードします。' -f $zipFileName)
    Write-Host '  3. ストアの掲載情報とプライバシーの項目を入力して、審査に提出します。'
    Write-Host '  ※ 更新するたびに manifest.json の version を前回より大きくしてください（同じ番号では出し直せません）。'
}

try {
    Invoke-Main
} catch {
    Write-Host ''
    Write-Host ('予期しないエラーが発生しました: {0}' -f $_.Exception.Message) -ForegroundColor Red
    if ($null -ne $_.InvocationInfo) {
        Write-Host $_.InvocationInfo.PositionMessage -ForegroundColor DarkGray
    }
    Write-Host 'パッケージの作成を中止しました。' -ForegroundColor Red
    exit 1
}
exit 0
