/* Word 引用插件（2.7.25）静态回归：只读源码/文档，不启动 Word、不打包、不安装。
 *
 * 目标：功能区 XML ↔ VBA 宏名接线、VBA 的本机 HTTP 与书签机制、模板产物、
 * 打包/安装/卸载接线、以及版本说明。任何一处漏提交，这里先红。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, statSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (rel) => readFileSync(join(root, rel), 'utf8');
const exists = (rel) => {
  try {
    statSync(join(root, rel));
    return true;
  } catch {
    return false;
  }
};

const XML = 'word/customUI.xml';
const BAS = 'word/PaperWhale.bas';
const DOTM = 'word/PaperWhaleMacros.dotm';

test('customUI.xml is a well-formed customUI document', () => {
  const xml = read(XML);
  assert.ok(xml.startsWith('<?xml'), 'must start with the XML declaration');
  assert.equal((xml.match(/<customUI\b/g) || []).length, 1, 'exactly one <customUI root');
  assert.equal((xml.match(/<\/customUI>/g) || []).length, 1, 'exactly one </customUI> close');
  assert.ok(
    xml.includes('xmlns="http://schemas.microsoft.com/office/2006/01/customui"'),
    'Word 2021 实测用 2006/01 命名空间（与 Zotero.dotm 一致）；2009/07 会被忽略',
  );
  assert.ok(xml.includes('</ribbon>') && xml.includes('</tabs>'),
    'the ribbon/tabs wrapper must be closed');
});

test('customUI.xml keeps the compatible tab id and NPL buttons', () => {
  const xml = read(XML);
  assert.match(xml, /<tab\b[^>]*id="PaperWhaleTab"[^>]*/, 'must keep the PaperWhaleTab id');
  assert.match(xml, /<tab\b[^>]*label="[^"]*NPL[^"]*"/, 'the tab label must mention NPL');
  // 按 id 或 label 检查（标签文案允许微调，例如「插入 / 更新参考文献」）。
  const required = [
    { feature: '插入引文', markers: ['插入引文', 'PWBtnInsert'] },
    { feature: '参考文献', markers: ['参考文献', 'PWBtnBib'] },
    { feature: '刷新', markers: ['刷新', 'PWBtnRefresh'] },
    { feature: '引文格式', markers: ['引文格式', 'PWBtnStyle'] },
    { feature: '测试连接', markers: ['测试连接', 'PWBtnTest'] },
  ];
  for (const { feature, markers } of required) {
    assert.ok(markers.some((marker) => xml.includes(marker)),
      `the ribbon must keep the「${feature}」button (by id or label)`);
  }
  // 至少要有一个按钮真的挂在 onAction 上，避免只放静态文字。
  assert.ok((xml.match(/onAction="[^"]+"/g) || []).length >= 5, 'buttons must be wired to macros');
});

test('every ribbon callback in customUI.xml has a Public Sub in PaperWhale.bas', () => {
  const xml = read(XML);
  const bas = read(BAS);
  const callbacks = [
    ...[...xml.matchAll(/onAction="([^"]+)"/g)].map((match) => match[1]),
    ...[...xml.matchAll(/onLoad="([^"]+)"/g)].map((match) => match[1]),
  ];
  assert.ok(callbacks.length > 0, 'customUI.xml must declare at least one callback');
  const missing = callbacks.filter((name) => !bas.includes(`Public Sub ${name}(`));
  assert.deepEqual(missing, [], `missing VBA handlers: ${missing.join(', ')}`);
});

test('PaperWhale.bas talks to the local service over MSXML2', () => {
  const bas = read(BAS);
  assert.match(bas, /^\s*Option Explicit\s*$/m, 'must declare Option Explicit');
  // 本机 HTTP 通道：首选 MSXML2（ServerXMLHTTP 能设超时），失败再退 WinHTTP。
  assert.match(bas, /MSXML2\.ServerXMLHTTP\.6\.0/i, 'ServerXMLHTTP 6.0 是首选通道');
  assert.match(bas, /MSXML2\.(?:Server)?XMLHTTP/i, 'must use an MSXML2 local HTTP channel');
  assert.ok(bas.includes('http://127.0.0.1'), 'must call the local service on 127.0.0.1');
});

test('PaperWhale.bas implements the bibliography bookmark round-trip', () => {
  const bas = read(BAS);
  assert.ok(bas.includes('/api/cite/document.txt'), 'must pull the rendered document text from the local API');
  assert.ok(bas.includes('--BIBLIOGRAPHY--'), 'must locate the bibliography marker in the document');
  assert.ok(bas.includes('PW_BOTTOM') || bas.includes('--BIBLIOGRAPHY--'),
    'the bibliography block must be bounded by the marker/bottom bookmark');
  assert.ok(bas.includes('ActiveDocument.Bookmarks'), 'must use document bookmarks to track citations');
});

test('PaperWhale.bas avoids external processes and non-local hosts', () => {
  const bas = read(BAS);
  assert.doesNotMatch(bas, /CreateObject\s*\(\s*"WScript\.Shell"\s*\)/i, 'must not spawn external processes');
  assert.doesNotMatch(bas, /WScript\.Shell/i, 'must not reference WScript.Shell');
  assert.doesNotMatch(bas, /https:\/\//i, 'only the local http:// endpoint is allowed');
  const hosts = [...bas.matchAll(/http:\/\/([^\s"'&)]+)/gi)]
    .map((match) => match[1].split('/')[0].split(':')[0])
    .filter(Boolean);
  const foreign = hosts.filter((host) => host !== '127.0.0.1');
  assert.deepEqual(foreign, [], `unexpected hosts: ${foreign.join(', ')}`);
});

test('the compiled macros template ships and is non-trivial', () => {
  assert.ok(exists(DOTM), 'word/PaperWhaleMacros.dotm must exist');
  const size = statSync(join(root, DOTM)).size;
  assert.ok(size > 20000, `PaperWhaleMacros.dotm is too small (${size} bytes); the ribbon/macros are probably missing`);
});

test('build.ps1 packages the word plugin into dist', () => {
  const script = read('build.ps1');
  assert.match(
    script,
    /Copy-Item\s+(?:-(?:Literal)?Path\s+)?["']word[\\/]/,
    'build.ps1 must copy the word template into dist',
  );
  assert.doesNotMatch(script, /word plugin disabled/i, 'the old "(word plugin disabled)" placeholder must be gone');
});

test('the installer calls install_word_plugin() and the uninstaller removes the dotm', () => {
  const setup = read('installer/setup.py');
  assert.ok(setup.includes('def install_word_plugin('), 'setup.py must keep install_word_plugin()');
  const match = /(^|\n)def install\s*\(/.exec(setup);
  assert.ok(match, 'setup.py must expose install(payload, target, version, ...)');
  const start = match.index + match[0].length;
  const rest = setup.slice(start);
  const nextDef = rest.search(/\ndef /);
  const body = nextDef >= 0 ? rest.slice(0, nextDef) : rest;
  assert.ok(body.includes('install_word_plugin('), 'install() must actually call install_word_plugin()');
  assert.match(setup, /Remove-Item[^\n]*PaperWhaleMacros\.dotm/,
    'the uninstall PowerShell must delete PaperWhaleMacros.dotm from Word STARTUP');
});

test('the 2.7.25 changelog section documents the Word plugin', () => {
  // 按内容定位 2.7.25 那一节（不再依赖「文件里第一个版本标题」，否则新版本会把它挤掉）
  const changelog = read('docs/版本更新.md');
  const start = changelog.indexOf('# NPL 2.7.25');
  assert.ok(start >= 0, 'the changelog must keep the "# NPL 2.7.25" section');
  const rest = changelog.slice(start);
  const nextHeading = rest.slice(rest.indexOf('\n')).search(/^#\s+/m);
  const section = nextHeading >= 0 ? rest.slice(0, rest.indexOf('\n') + nextHeading) : rest;
  assert.ok(section.includes('Word'), 'the 2.7.25 section must mention the Word plugin');
});

test('README documents the Word plugin', () => {
  const readme = read('README.md');
  assert.match(readme, /Word[^\n]{0,20}插件|插件[^\n]{0,20}Word/, 'README must mention the Word 插件');
});

/* ============================================================
 * Zotero 式「插入引文」弹窗：UserForm PWCitationPicker
 *   窗体控件全部在 UserForm_Initialize 里动态创建；数据来自
 *   GET /api/word/papers.txt（TSV），支持搜索 / 多选 / 全选 / 双击。
 * ============================================================ */
const PICKER = 'word/PWCitationPicker.code.bas';
const BUILD_WORD = 'word/build_word_plugin.ps1';

test('the citation picker form module ships with the Zotero-style list config', () => {
  assert.ok(exists(PICKER), 'word/PWCitationPicker.code.bas must exist');
  const picker = read(PICKER);
  assert.ok(picker.includes('MultiSelect = 2'), 'the list must allow extended (Ctrl/Shift) multi-select');
  assert.ok(picker.includes('ColumnCount = 4'), 'the list must show four columns');
  assert.ok(picker.includes('ColumnWidths'), 'the columns must have explicit widths');
  assert.ok(picker.includes('/api/word/papers.txt'), 'the form must load the local Word papers endpoint');
  assert.ok(picker.includes('limit=500'), 'the form must ask for up to 500 papers');
  assert.ok(picker.includes('gPickerIds'), 'the form must publish the selected ids to the standard module');
  assert.ok(picker.includes('PWUrlEncode'), 'the search query must be URL-encoded');
  assert.equal(picker.includes('InputBox'), false, 'the picker must replace InputBox, not wrap it');
});

test('the picker builds all eight controls dynamically inside UserForm_Initialize', () => {
  const picker = read(PICKER);
  assert.ok(picker.includes('Private Sub UserForm_Initialize()'), 'must build the UI in UserForm_Initialize');
  assert.ok(picker.includes('Me.Controls.Add("'), 'controls must be added with Me.Controls.Add');
  const count = (needle) => picker.split(needle).length - 1;
  assert.ok(count('"Forms.TextBox.1"') >= 1, 'a search textbox must be created');
  assert.ok(count('"Forms.ListBox.1"') >= 1, 'the paper listbox must be created');
  assert.ok(count('"Forms.CommandButton.1"') >= 4, 'search / select-all / insert / cancel buttons are required');
  assert.ok(count('"Forms.Label.1"') >= 2, 'status and tip labels are required');
  const total = (picker.match(/Me\.Controls\.Add\(/g) || []).length;
  assert.ok(total >= 8, `expected at least 8 dynamically created controls, got ${total}`);
});

test('the picker supports select-all, double-click insert, Enter search and ordered ids', () => {
  const picker = read(PICKER);
  assert.ok(picker.includes('Private Sub btnAll_Click()'), 'must have a select-all handler');
  assert.ok(picker.includes('lstPapers.Selected(i) = True'), 'select-all must select every row');
  assert.ok(picker.includes('Private Sub lstPapers_DblClick'), 'double-click must insert the row');
  assert.ok(picker.includes('Private Sub txtQuery_KeyDown'), 'Enter in the search box must trigger a search');
  assert.match(picker, /KeyCode = 13/, 'Enter (VK 13) must be handled');
  assert.ok(picker.includes('Private Function PWSelectionIds()'), 'selected ids must be collected in a helper');
  assert.match(picker, /For i = 0 To lstPapers\.ListCount - 1/, 'ids must be collected in list order');
  assert.ok(picker.includes('mIds(i)'), 'each visible row must map back to its paper id');
  assert.ok(picker.includes('gPickerIds = ids'), 'confirming must publish the joined ids');
  assert.match(picker, /btnClose_Click[\s\S]{0,160}gPickerIds = ""/, 'cancel must clear the published ids');
});

test('the picker parses the documented TSV response shape', () => {
  const picker = read(PICKER);
  assert.ok(picker.includes('vbTab'), 'the response must be split by tab');
  assert.match(picker, /Left\$\s*\(\s*lines\(i\),\s*1\s*\)\s*=\s*"#"/, 'the #count header line must be detected');
  assert.ok(picker.includes('parts(1)') && picker.includes('parts(2)'),
    'the header must expose total and returned counts');
  assert.ok(picker.includes('parts(5)'), 'the has_pdf column must be read');
  assert.ok(picker.includes('ListCount'), 'the loaded rows must be counted from the listbox');
});

test('PWInsertCitation opens the web picker through a one-time token hyperlink', () => {
  const bas = read(BAS);
  const start = bas.indexOf('Public Sub PWInsertCitation(');
  assert.ok(start >= 0, 'PaperWhale.bas must keep PWInsertCitation()');
  const body = bas.slice(start, bas.indexOf('End Sub', start));
  assert.ok(body.includes('token = PWNewToken()'), 'each invocation must mint a fresh one-time token');
  assert.ok(body.includes('gPickToken = token'), 'the token must be exposed for automation/debugging');
  assert.ok(body.includes('PWSetDocVar "PW_PICK_TOKEN", token'),
    'the token must be remembered in a document variable');
  assert.ok(body.includes('url = PWBase() & "/word-picker?token=" & token'),
    'the picker URL must be the local /word-picker page carrying the token');
  assert.ok(body.includes('ActiveDocument.FollowHyperlink Address:=url'),
    'the picker must open in the default browser through a hyperlink');
  assert.equal(body.includes('PWCitationPicker.Show'), false,
    'the VBA form must no longer be shown from PWInsertCitation');
  assert.ok(body.includes('PWInsertCitationGroup'), 'the returned ids must flow into the citation writer');
  // 旧的选择器 InputBox 链路必须消失（PWSetPort 的端口 InputBox 仍要保留）。
  for (const gone of ['PWChoosePicks', 'PWItemId', 'PWItemLabel', '输入要插入的序号']) {
    assert.equal(bas.includes(gone), false, `the old picker symbol ${gone} must be gone`);
  }
  assert.equal(/\bPWSearch\s*\(/.test(bas), false, 'the old PWSearch() selector must be gone');
  assert.ok(bas.includes('InputBox("NPL 本机端口'), 'PWSetPort must keep its InputBox');
});

test('PWInsertCitation polls /api/word/pick and handles cancel and timeout', () => {
  const bas = read(BAS);
  const start = bas.indexOf('Public Sub PWInsertCitation(');
  const body = bas.slice(start, bas.indexOf('End Sub', start));
  assert.ok(body.includes('raw = PWGet("/api/word/pick?token=" & token)'),
    'the result must be polled from the local pick endpoint');
  assert.ok(body.includes('DoEvents'), 'DoEvents must keep Word responsive while polling');
  assert.ok(body.includes('PWSleepMs 150'), 'the poll interval must be 150 ms');
  assert.ok(body.includes('waitSec = 240') && body.includes('Timer - t0 > waitSec'),
    'the poll loop must give up after 240 seconds');
  assert.ok(body.includes('If raw = "__CANCELLED__" Then Exit Sub'),
    'a cancel in the browser must abort silently');
  assert.ok(body.includes('gPickerIds = raw'), 'the joined ids must be picked up from the response');
  assert.ok(body.includes('PWInsertCitationGroup ids'), 'the ids must be inserted once the poll returns');
  assert.ok(body.includes('PWRefreshAll(False)'), 'the document must be refreshed after inserting');
});

test('PWNewToken and PWSleepMs support fixed-token tests and sleep between polls', () => {
  const bas = read(BAS);
  const start = bas.indexOf('Private Function PWNewToken(');
  assert.ok(start >= 0, 'the one-time token generator must exist');
  const body = bas.slice(start, bas.indexOf('End Function', start));
  assert.ok(body.includes('PWGetDocVar("PW_PICK_TOKEN_OVERRIDE")'),
    'E2E tests must be able to pin the token through a document variable');
  assert.ok(body.includes('PWNewToken = fixed'), 'the override must be returned verbatim');
  assert.ok(body.includes('Randomize'), 'the real token must include randomness');
  assert.ok(body.includes('Format$(Now, "yyyymmddhhnnss")'), 'the token must embed a timestamp');
  assert.ok(bas.indexOf('Private Sub PWSleepMs(') >= 0,
    'PWSleepMs must exist so polling does not freeze Word');
  assert.ok(bas.includes('#If VBA7 Then') && bas.includes('#Else') && bas.includes('#End If'),
    'the Sleep declaration must be guarded for 64-bit and 32-bit Office');
  assert.ok(bas.includes('Private Declare PtrSafe Sub Sleep Lib "kernel32"'),
    'VBA7 (64-bit Office) must declare the PtrSafe kernel32 Sleep');
  assert.ok(bas.includes('Private Declare Sub Sleep Lib "kernel32"'),
    'the 32-bit fallback declaration must exist');
  assert.ok(bas.indexOf('Sleep ms') > bas.indexOf('Private Sub PWSleepMs('),
    'PWSleepMs must actually call the declared Sleep');
});

test('the standard module exposes PWGet/PWUrlEncode for the form and shares the picker globals', () => {
  const bas = read(BAS);
  assert.match(bas, /Public Function PWGet\(/, 'the form calls PWGet, so it must be public');
  assert.match(bas, /Public Function PWUrlEncode\(/, 'the form calls PWUrlEncode, so it must be public');
  assert.match(bas, /^Public gPickerQuery As String$/m, 'gPickerQuery must be public');
  assert.match(bas, /^Public gPickerIds As String$/m, 'gPickerIds must be public');
});

test('the Word build script injects the picker form and keeps the standard module', () => {
  const build = read(BUILD_WORD);
  assert.ok(build.includes('VBComponents.Add(3)'), 'the build must add a UserForm component (type 3)');
  assert.ok(build.includes('PWCitationPicker.code.bas'), 'the build must inject the picker code module');
  assert.ok(build.includes('"PWCitationPicker"'), 'the form must be named PWCitationPicker');
  assert.ok(build.includes('VBComponents.Add(1)'), 'the standard module must still be added');
  assert.ok(build.includes('"PaperWhale"'), 'the standard module must still be named PaperWhale');
  assert.ok(build.includes('PaperWhale.bas'), 'the standard module source must still be injected');
  assert.match(build, /Type -eq 3/, 'stale form components must be removed before injecting the new one');
});

test('the Word build script stays ASCII-only (PowerShell 5.1 reads it without a BOM)', () => {
  const bytes = readFileSync(join(root, BUILD_WORD));
  for (let index = 0; index < bytes.length; index += 1) {
    assert.ok(bytes[index] < 0x80,
      `non-ASCII byte 0x${bytes[index].toString(16)} at offset ${index}; keep build_word_plugin.ps1 ASCII-only`);
  }
});

/* ============================================================
 * 分类筛选 + 主程序配色：PWCitationPicker 2.7.31 的两项新能力
 *   分类：GET /api/word/categories.txt（#stats + depth/path/count）
 *   过滤：GET /api/word/papers.txt?q=&scope=&limit=500
 * ============================================================ */

test('the picker puts the categories in a left-hand list box', () => {
  const picker = read(PICKER);
  assert.ok(picker.includes('Me.Controls.Add("Forms.ListBox.1", "lstCategories"'),
    'the category pane must be a ListBox named lstCategories');
  assert.ok(picker.includes('Private WithEvents lstCategories As MSForms.ListBox'),
    'the form must keep the WithEvents list field');
  assert.equal(picker.includes('Forms.ComboBox.1'), false,
    'the Zotero-style layout replaced the category combo box with a left-hand list');
  assert.equal(picker.includes('cboCategory'), false, 'the old cboCategory symbol must be gone');
});

test('the picker loads the category tree from the documented endpoint', () => {
  const picker = read(PICKER);
  assert.ok(picker.includes('/api/word/categories.txt'), 'must GET /api/word/categories.txt');
  assert.ok(picker.includes('#stats'), 'must detect the #stats header line');
  assert.ok(picker.includes('papers = Val(parts(1))'), 'the #stats line must carry the paper total');
  assert.ok(picker.includes('uncat = Val(parts(2))'), 'the #stats line must carry the uncategorized count');
  assert.ok(picker.includes('leaf = parts(1)'), 'each category row must carry its full path');
  assert.ok(picker.includes('InStrRev(parts(1), " / ")'), 'the label must keep only the leaf segment');
  assert.ok(picker.includes('mScopes(1) = "__none__"'), '__none__ must map to the uncategorized scope');
});

test('the papers request carries the encoded scope and still caps at 500', () => {
  const picker = read(PICKER);
  assert.ok(picker.includes('&scope='), 'the query string must append &scope=');
  assert.ok(picker.includes('PWUrlEncode(scope)'), 'the scope value must be URL-encoded');
  assert.ok(picker.includes('"__none__"'), 'the uncategorized scope literal must exist');
  assert.match(picker, /path = path & "&limit=500"/, 'the scope must not drop the 500-row cap');
  assert.ok(picker.includes('/api/word/papers.txt?q='), 'the papers endpoint must keep the q parameter first');
});

test('selecting a category reloads the list without recursing', () => {
  const picker = read(PICKER);
  assert.ok(picker.includes('Private Function PWCurrentScope()'), 'must map the selected category index to a scope');
  assert.ok(picker.includes('Private Sub lstCategories_Click()'), 'must react to category selection');
  assert.match(picker, /lstCategories_Click\(\)[\s\S]{0,160}PWLoadPapers txtQuery\.Value, PWCurrentScope\(\)/,
    'selecting a category must reload the papers with the new scope');
  assert.match(picker, /Private Sub lstCategories_Click\(\)[\s\S]{0,120}If mLoadingCats Then Exit Sub/,
    'mLoadingCats must prevent a reload loop while the list is being populated');
  assert.ok(picker.includes('mLoadingCats = True'), 'populating the list must raise the loading flag');
  assert.ok(picker.includes('mLoadingCats = False'), 'the loading flag must be cleared afterwards');
});

test('category labels are drawn as a tree with │ / ├─ / └─ guides', () => {
  const picker = read(PICKER);
  const start = picker.indexOf('Private Function PWTreePrefix(');
  assert.ok(start >= 0, 'the category pane needs a PWTreePrefix() helper');
  assert.ok(
    picker.includes(
      'Private Function PWTreePrefix(depths() As Long, ByVal count As Long, ByVal i As Long) As String',
    ),
    'the helper must take the depth array, the row count and the row index',
  );
  const body = picker.slice(start, picker.indexOf('End Function', start));
  assert.ok(body.includes('ChrW(&H2502)'), '│ must draw the ancestor guide');
  assert.ok(body.includes('ChrW(&H251C)'), '├ must mark a node that has following siblings');
  assert.ok(body.includes('ChrW(&H2514)'), '└ must mark the last node of its level');
  assert.ok(body.includes("' │") && body.includes("' ├─") && body.includes("' └─"),
    'each box-drawing glyph must stay documented next to its character code');
  assert.ok(picker.includes('labels(i) = PWTreePrefix(depths, n, i) & leafs(i) & "（" & cnts(i) & " 篇）"'),
    'each row label must combine the tree prefix, the leaf name and the subtree count');
  assert.ok(picker.includes('leafs(n) = leaf') && picker.includes('depths(n) = Val(parts(0))'),
    'leaf names and depths must be collected before the prefix pass');
  assert.ok(picker.includes('leaf = Mid$(parts(1), pos + 3)'),
    'the label must show only the leaf segment of the full path');
  assert.equal(picker.includes('String$(Val(parts(0)) * 2, " ")'), false,
    'the flat space indentation must be gone in favour of tree guides');
});

test('a failing categories request degrades to a single 我的文库 entry', () => {
  const picker = read(PICKER);
  const loadCats = picker.slice(picker.indexOf('Private Sub PWLoadCategories()'));
  const errBranch = loadCats.slice(loadCats.indexOf('If Left$(raw, 4) = "ERR:"'));
  assert.ok(errBranch.slice(0, 400).includes('lstCategories.AddItem "我的文库"'),
    'the ERR branch must add a single 我的文库 entry so the dialog still opens');
  assert.match(errBranch.slice(0, 400), /mLoadingCats = False/, 'the ERR branch must clear the loading flag');
});

test('the picker applies the main app colour theme', () => {
  const picker = read(PICKER);
  assert.ok(picker.includes('Me.BackColor = RGB(244, 246, 249)'), 'window background must match the app shell');
  assert.ok(picker.includes('lstPapers.BackColor = vbWhite'), 'the list must be white');
  assert.ok(picker.includes('lstPapers.ForeColor = RGB(16, 32, 58)'), 'list text must be dark navy');
  assert.ok(picker.includes('lstCategories.BackColor = vbWhite'), 'the category list must be white as well');
  assert.ok(picker.includes('lstCategories.ForeColor = RGB(16, 32, 58)'), 'category text must be dark navy');
  assert.ok(picker.includes('btnInsert.BackColor = RGB(14, 124, 134)'), 'the primary button must be teal');
  assert.ok(picker.includes('btnInsert.ForeColor = vbWhite'), 'the primary button text must be white');
  assert.ok(picker.includes('btnInsert.Font.Bold = True'), 'the primary button text must be bold');
  assert.match(picker, /labTip\.ForeColor = RGB\(107, 124, 143\)/, 'tip text must use the muted grey');
  assert.match(picker, /labStatus\.ForeColor = RGB\(107, 124, 143\)/, 'status text must use the muted grey');
  assert.match(picker, /labHeadYear\.ForeColor = RGB\(16, 32, 58\)/, 'column headers must use dark navy');
  const dividerBlock = picker.slice(picker.indexOf('Set labDivider = Me.Controls.Add('));
  const dividerInit = dividerBlock.slice(0, dividerBlock.indexOf('gPickerStage'));
  assert.ok(dividerInit.includes('.BackColor = RGB(226, 232, 240)'),
    'the divider must use the app line colour');
  assert.ok(dividerInit.includes('.BackStyle = 1'), 'the divider must be an opaque bar');
});

test('the theme is applied before the category list is loaded', () => {
  const picker = read(PICKER);
  const init = picker.slice(picker.indexOf('Private Sub UserForm_Initialize()'));
  const initBody = init.slice(0, init.indexOf('End Sub'));
  const themeAt = initBody.indexOf('PWApplyTheme');
  const catsAt = initBody.indexOf('PWLoadCategories');
  assert.ok(themeAt >= 0, 'UserForm_Initialize must call PWApplyTheme');
  assert.ok(catsAt >= 0, 'UserForm_Initialize must call PWLoadCategories');
  assert.ok(themeAt < catsAt, 'PWApplyTheme must run before PWLoadCategories');
});

test('the design-time size stays 880 x 540 but PWFitWindow adapts it at runtime', () => {
  const picker = read(PICKER);
  const build = read(BUILD_WORD);
  assert.match(build, /\$form\.Properties\.Item\("Width"\)\.Value = 880/,
    'the designer default stays 880 px wide (overridden at runtime)');
  assert.match(build, /\$form\.Properties\.Item\("Height"\)\.Value = 540/,
    'the designer default stays 540 px tall (overridden at runtime)');
  const start = picker.indexOf('Private Sub PWFitWindow()');
  assert.ok(start >= 0, 'the form must recompute its own size at runtime');
  const body = picker.slice(start, picker.indexOf('End Sub', start));
  assert.ok(body.includes('maxH = 540') && body.includes('maxW = 880'),
    'the runtime caps must start from the designer defaults');
  assert.ok(
    body.includes('If Application.ActiveWindow.Height - 60 < maxH Then maxH = Application.ActiveWindow.Height - 60'),
    'the height must follow the Word window minus a 60 px margin',
  );
  assert.ok(
    body.includes('If Application.ActiveWindow.Width - 60 < maxW Then maxW = Application.ActiveWindow.Width - 60'),
    'the width must follow the Word window minus a 60 px margin',
  );
  assert.match(body, /If maxH < 380 Then maxH = 380/, 'a minimum height keeps the dialog usable');
  assert.match(body, /If maxW < 720 Then maxW = 720/, 'a minimum width keeps the dialog usable');
  assert.ok(body.includes('Me.Height = maxH') && body.includes('Me.Width = maxW'),
    'the computed size must be applied to the form');
});

/* ============================================================
 * Zotero 式布局重排（2.7.32）：左侧分类列表 / 四列表格 / 贴底 footer
 * ============================================================ */

test('the picker follows the Zotero layout: search row, category pane, four headers', () => {
  const picker = read(PICKER);
  // 顶部：搜索标签 + 输入框 + 搜索 / 全选按钮
  for (const control of ['labSearchTag', 'txtQuery', 'btnSearch', 'btnAll']) {
    assert.ok(picker.includes(`"${control}"`), `the search row must keep ${control}`);
  }
  // 左侧：分类标题「我的文库」+ 分类列表
  assert.ok(picker.includes('"labCatTag"'), 'the left pane needs a heading label');
  assert.ok(picker.includes('labCatTag.Caption = "我的文库"'), 'the left pane must be titled 我的文库');
  // 右侧：四个表头（共用一个样式 helper）
  const headers = [
    ['labHeadTitle', '标题'],
    ['labHeadAuthors', '作者'],
    ['labHeadYear', '年份'],
    ['labHeadVenue', '期刊'],
  ];
  for (const [control, label] of headers) {
    assert.ok(picker.includes(`"${control}"`), `the ${label} header control must exist`);
    assert.ok(picker.includes(`PWHeadStyle ${control}, "${label}"`),
      `the ${label} header must be captioned ${label}`);
  }
  assert.ok(
    picker.includes('Private Sub PWHeadStyle(ByVal lbl As MSForms.Label, ByVal text As String)'),
    'the four headers must share one styling helper',
  );
  // 底部：分隔线 / 提示 / 状态 / 插入 / 取消
  for (const control of ['labDivider', 'labTip', 'labStatus', 'btnInsert', 'btnClose']) {
    assert.ok(picker.includes(`"${control}"`), `the footer must keep ${control}`);
  }
});

test('PWFitLayout lays out every pane from the real form size at runtime', () => {
  const picker = read(PICKER);
  const start = picker.indexOf('Private Sub PWFitLayout()');
  assert.ok(start >= 0, 'the form must keep PWFitLayout');
  const body = picker.slice(start, picker.indexOf('End Sub', start));
  // UserForm 在 Show 之前 InsideWidth/InsideHeight 不可靠，实现用设计尺寸减边框反推。
  assert.ok(body.includes('w = Me.Width - 6') && body.includes('h = Me.Height - 24'),
    'the layout must derive the client area from the real form size, not the design-time constants');
  assert.ok(body.includes('UserForm 在 Show 之前 InsideWidth/InsideHeight 不可靠'),
    'the reason for not trusting InsideWidth before Show must stay documented');
  assert.ok(body.includes('listH = (h - 68) - mPaneTop'),
    'the two lists must fill the space between the header row and the footer');
  assert.ok(body.includes('btnClose.Move w - EDGE - 68, h - 34, 68, 26'),
    'the cancel button must stick to the bottom-right corner');
  assert.ok(body.includes('btnInsert.Move btnClose.Left - 8 - 100, h - 34, 100, 26'),
    'the insert button must sit left of cancel on the same baseline');
  assert.ok(body.includes('labStatus.Move EDGE, h - 40'), 'the status line must sit above the bottom edge');
  assert.ok(body.includes('labDivider.Move EDGE, h - 64, w - 2 * EDGE, 1'),
    'the divider must span the client width');
  assert.ok(body.includes('lstCategories.Move EDGE, mPaneTop, CAT_W, listH'),
    'the category list must fill the left pane');
  assert.ok(body.includes('lstPapers.Move mPaperX, mPaneTop, mPaperW, listH'),
    'the paper list must fill the right pane');
  assert.ok(body.includes('mTitleW = mPaperW - AUTH_W - YEAR_W - VEN_W - 17'),
    'the title column must absorb the remaining width');
  assert.match(
    body,
    /lstPapers\.ColumnWidths = CStr\(mTitleW\) & ";" & CStr\(AUTH_W\) & ";" & CStr\(YEAR_W\) & ";" & CStr\(VEN_W\)/,
    'the column widths must be rebuilt at runtime from the measured widths',
  );
  assert.ok(body.indexOf('ColumnWidths = CStr(mTitleW)') < body.indexOf('lstPapers.Move'),
    'column widths must be set before the list is moved (MSForms resets the control width otherwise)');
  assert.match(body, /If listH < 80 Then listH = 80/, 'a minimum list height keeps rows readable');
});

test('the paper list shows title, authors, year and venue in that column order', () => {
  const picker = read(PICKER);
  const start = picker.indexOf('Private Sub PWLoadPapers(');
  assert.ok(start >= 0, 'the form must keep PWLoadPapers');
  const body = picker.slice(start, picker.indexOf('End Sub', start));
  assert.match(body, /lstPapers\.AddItem "\[无PDF\] " & parts\(3\)/,
    'a paper without a PDF must be flagged in front of the title (row tails get clipped)');
  assert.match(body, /lstPapers\.AddItem parts\(3\)/, 'the first column must be the title');
  assert.match(body, /lstPapers\.List\(row, 1\) = parts\(2\)/, 'column 2 must be the authors');
  assert.match(body, /lstPapers\.List\(row, 2\) = parts\(1\)/, 'column 3 must be the year');
  assert.match(body, /lstPapers\.List\(row, 3\) = parts\(4\)/, 'column 4 must be the venue');
  assert.ok(picker.includes('.ColumnCount = 4'), 'the list must still have four columns');
});

test('the form is titled like Zotero and boots in size -> layout -> theme -> categories -> papers order', () => {
  const picker = read(PICKER);
  assert.ok(picker.includes('Me.Caption = "添加引注 · NPL"'),
    'the window title must be 添加引注 · NPL');
  const init = picker.slice(picker.indexOf('Private Sub UserForm_Initialize()'));
  const initBody = init.slice(0, init.indexOf('End Sub'));
  const stages = ['PWFitWindow', 'PWFitLayout', 'PWApplyTheme', 'PWLoadCategories', 'PWLoadPapers'];
  const positions = stages.map((name) => initBody.indexOf(name));
  for (let index = 0; index < stages.length; index += 1) {
    assert.ok(positions[index] >= 0, `UserForm_Initialize must call ${stages[index]}`);
  }
  for (let index = 1; index < stages.length; index += 1) {
    assert.ok(positions[index - 1] < positions[index],
      `${stages[index - 1]} must run before ${stages[index]} (size first, then controls, theme and data)`);
  }
  assert.match(initBody, /gPickerStage = "size"[\s\S]{0,60}PWFitWindow/,
    'the size stage marker must be set before adapting the window (for failure diagnosis)');
});

/* ============================================================
 * 序号式引文（2.7.33）：document.txt 三列 + [1] / [1,2] / [1-3] 折叠
 *   后端每行 id <TAB> index <TAB> inline；VBA 用 index 折叠编号，
 *   数字型格式由 styles.txt 第三列（1/0）决定，默认格式 ieee。
 * ============================================================ */

test('document.txt is parsed as id / index / inline (three columns)', () => {
  const bas = read(BAS);
  const start = bas.indexOf('Private Function PWDocumentReply(');
  assert.ok(start >= 0, 'PaperWhale.bas must keep PWDocumentReply()');
  const body = bas.slice(start, bas.indexOf('End Function', start));
  assert.ok(body.includes('tabPos = InStr(line, vbTab)'), 'the line must be split on the first tab');
  assert.ok(body.includes('rest = Mid$(line, tabPos + 1)'), 'the remainder must carry index + inline text');
  assert.ok(body.includes('tab2 = InStr(rest, vbTab)'), 'the remainder must be split on the second tab');
  assert.ok(body.includes('idx = Val(Left$(rest, tab2 - 1))'), 'the middle column is the numeric index');
  assert.ok(body.includes('body = Mid$(rest, tab2 + 1)'), 'the last column is the inline citation');
  assert.ok(body.includes('idx = 0') && body.includes('body = rest'),
    'legacy two-column replies must still parse (index 0)');
  assert.ok(body.includes('map(Left$(line, tabPos - 1)) = idx & Chr(1) & body'),
    'the dictionary value must encode index and inline text');
  assert.ok(body.includes('map = CreateObject("Scripting.Dictionary")'),
    'per-paper lookups go through a dictionary');
});

test('PWEntryIndex/PWEntryInline unpack the encoded map value', () => {
  const bas = read(BAS);
  const indexFn = bas.slice(bas.indexOf('Private Function PWEntryIndex('));
  const indexBody = indexFn.slice(0, indexFn.indexOf('End Function'));
  assert.ok(indexBody.includes('pos = InStr(entry, Chr(1))'), 'the index is separated by Chr(1)');
  assert.ok(indexBody.includes('Val(Left$(entry, pos - 1))'), 'the prefix before Chr(1) is the index');
  const inlineFn = bas.slice(bas.indexOf('Private Function PWEntryInline('));
  const inlineBody = inlineFn.slice(0, inlineFn.indexOf('End Function'));
  assert.ok(inlineBody.includes('Mid$(entry, pos + 1)'), 'the suffix after Chr(1) is the inline text');
  const buildFn = bas.slice(bas.indexOf('Private Function PWBuildCitationText('));
  const buildBody = buildFn.slice(0, buildFn.indexOf('End Function'));
  assert.ok(buildBody.includes('idx = PWEntryIndex(entry)') && buildBody.includes('one = PWEntryInline(entry)'),
    'PWBuildCitationText must use both helpers');
});

test('PWNumericBracket folds runs of three or more and comma-joins the rest', () => {
  const bas = read(BAS);
  const start = bas.indexOf('Private Function PWNumericBracket(');
  assert.ok(start >= 0, 'must expose an index-bracket builder');
  const body = bas.slice(start, bas.indexOf('End Function', start));
  assert.ok(bas.includes("' 数字型引文折叠：[1]、[1,2]、[1-3]、[1-3,5]"),
    'the documented cases ([1] / [1,2] / [1-3] / [1-3,5]) must stay next to the algorithm');
  assert.match(body, /If nums\(j\) > nums\(j \+ 1\) Then/, 'the indices must be sorted ascending first');
  assert.ok(body.includes('If nums(i + runLen) = runStart + runLen Then'),
    'consecutive runs must be detected');
  assert.match(body, /If runLen >= 3 Then/, 'only runs of three or more collapse to a range');
  assert.ok(body.includes('out = out & CStr(runStart) & "-" & CStr(runStart + runLen - 1)'),
    'a collapsed run must render as a-b');
  assert.ok((body.match(/out = out & ","/g) || []).length >= 2,
    'shorter runs must be comma-joined instead of ranged');
  assert.ok(body.includes('PWNumericBracket = "[" & out & "]"'),
    'the result must be wrapped in square brackets');
});

test('PWIsNumericStyle reads the third styles.txt column and caches it', () => {
  const bas = read(BAS);
  const start = bas.indexOf('Private Function PWIsNumericStyle(');
  assert.ok(start >= 0, 'must decide numeric vs author-date styles');
  const body = bas.slice(start, bas.indexOf('End Function', start));
  assert.ok(body.includes('PWGet("/api/cite/styles.txt")'), 'must read the local styles list');
  assert.ok(body.includes('If gNumStyleKey <> style Then'), 'the answer must be cached per style');
  assert.ok(body.includes('gNumStyleKey = style'), 'the cache key must be the style id');
  assert.ok(body.includes('gNumStyleVal = 0'), 'a missing style must default to non-numeric');
  assert.ok(body.includes('If parts(0) = style Then'), 'the row must be matched on the style id');
  assert.ok(body.includes('gNumStyleVal = Val(parts(2))'), 'the third column is the numeric flag');
  assert.ok(body.includes('PWIsNumericStyle = (gNumStyleVal = 1)'), 'flag 1 means numeric');
});

test('numeric style is resolved before replacing citations and ieee is the default', () => {
  const bas = read(BAS);
  assert.match(bas, /^Private gNumericStyle As Long$/m, 'the module must cache the numeric-style flag');
  const refresh = bas.slice(bas.indexOf('Private Function PWRefreshAll('));
  const refreshBody = refresh.slice(0, refresh.indexOf('End Function'));
  const resolveAt = refreshBody.indexOf('gNumericStyle = PWIsNumericStyle(style)');
  assert.ok(resolveAt >= 0, 'PWRefreshAll must resolve the numeric flag before touching bookmarks');
  assert.ok(resolveAt < refreshBody.indexOf('PWBuildCitationText'),
    'the flag must be known before any citation text is rebuilt');
  const buildFn = bas.slice(bas.indexOf('Private Function PWBuildCitationText('));
  const buildBody = buildFn.slice(0, buildFn.indexOf('End Function'));
  assert.ok(buildBody.includes('If gNumericStyle Then'), 'numeric styles take a dedicated branch');
  assert.ok(buildBody.includes('PWNumericBracket(nums, numCount)'),
    'the numeric branch delegates to the bracket builder');
  const styleFn = bas.slice(bas.indexOf('Private Function PWStyle('));
  const styleBody = styleFn.slice(0, styleFn.indexOf('End Function'));
  assert.ok(styleBody.includes('GetSetting(PW_SETTINGS_APP, "Config", "Style", "ieee")'),
    'the saved preference default must be ieee');
  assert.ok((styleBody.match(/"ieee"/g) || []).length >= 2,
    'an empty saved preference must fall back to ieee as well');
});

/* ============================================================
 * 引文格式改成可搜索列表窗体（2.7.34）：PWStylePicker
 *   数据：GET /api/cite/styles.txt（id <TAB> name <TAB> 数字型?）
 *   交互：搜索过滤、当前格式标记、数字型标记、OK/取消回传 gStylePicked
 * ============================================================ */
const STYLE_PICKER = 'word/PWStylePicker.code.bas';

test('the style picker form ships as a searchable list dialog', () => {
  assert.ok(exists(STYLE_PICKER), 'word/PWStylePicker.code.bas must exist');
  const form = read(STYLE_PICKER);
  assert.ok(form.includes('Me.Caption = "NPL 引文格式"'), 'the dialog must be titled 引文格式');
  for (const control of ['txtSearch', 'lstStyles', 'btnOK', 'btnCancel', 'labHint', 'labSearchTag']) {
    assert.ok(form.includes(`"${control}"`), `the style dialog must create ${control}`);
  }
  assert.equal(/\bInputBox\s*\(/.test(form), false, 'the style picker must replace the numbered InputBox call');
  assert.ok(form.includes('PWGet("/api/cite/styles.txt")'), 'the list must come from the local styles endpoint');
  assert.ok(form.includes('mNumeric(mCount) = (Val(parts(2)) = 1)'),
    'the third column marks numeric styles');
  assert.ok(form.includes('label = label & "（数字型）"'), 'numeric styles must be labelled');
  assert.ok(form.includes('label = label & "   ← 当前"'), 'the current style must be marked');
  assert.ok(form.includes('gStylePicked = mIds(idx)') && form.includes('gStylePickedName = mNames(idx)'),
    'accepting must publish both the chosen id and its display name');
  assert.ok(form.includes('gStylePicked = ""'), 'cancel must clear the picked id');
});

test('PWFilterStyles filters on name and id while the user types', () => {
  const form = read(STYLE_PICKER);
  const start = form.indexOf('Private Sub PWFilterStyles(');
  assert.ok(start >= 0, 'the dialog must keep PWFilterStyles');
  const body = form.slice(start, form.indexOf('End Sub', start));
  assert.ok(body.includes('q = LCase$(Trim$(q))'), 'the query must be trimmed and lower-cased');
  assert.ok(body.includes('InStr(LCase$(mNames(i)), q) > 0'), 'the display name must be searchable');
  assert.ok(body.includes('InStr(LCase$(mIds(i)), q) > 0'), 'the style id must be searchable too');
  assert.ok(body.includes('If Len(q) = 0 Or'), 'an empty query must show every style');
  assert.ok(body.includes('lstStyles.ListCount = 1 And mIds(i) = gStyleCurrent'),
    'the current style must be pre-selected when it is the only match');
  assert.ok(body.includes('If lstStyles.ListCount = 0 Then labHint.Caption = "没有匹配的格式。"'),
    'an empty result must explain itself');
  assert.ok(form.includes('Private Sub txtSearch_Change()'), 'typing must re-filter immediately');
  assert.ok(form.includes('PWFilterStyles txtSearch.Value'), 'the change handler must pass the typed text');
  assert.ok(form.includes('Private Sub txtSearch_KeyDown'), 'Enter must accept the highlighted style');
  assert.ok(form.includes('Private Sub lstStyles_DblClick'), 'double-click must accept the highlighted style');
});

test('PWSetStyle delegates to the style picker and persists the choice', () => {
  const bas = read(BAS);
  const start = bas.indexOf('Public Sub PWSetStyle()');
  assert.ok(start >= 0, 'PaperWhale.bas must keep PWSetStyle()');
  const body = bas.slice(start, bas.indexOf('End Sub', start));
  assert.ok(body.includes('gStyleCurrent = PWStyle()'), 'the dialog must know the current style first');
  assert.ok(body.includes('PWStylePicker.Show vbModal'), 'PWSetStyle must open the style picker modally');
  assert.equal(body.includes('InputBox'), false, 'the numbered InputBox selector must be gone');
  assert.ok(body.includes('PWSetDocVar STYLE_VAR, gStylePicked'), 'the choice must be stored in the document');
  assert.ok(body.includes('SaveSetting PW_SETTINGS_APP, "Config", "Style", gStylePicked'),
    'the choice must also be remembered in the registry');
  assert.ok(body.includes('If PWCitationCount() > 0 Then PWRefreshAll True'),
    'existing citations must be re-rendered with the new style');
  assert.match(bas, /^Public gStylePicked As String$/m, 'the picked id must be shared with the form');
  assert.match(bas, /^Public gStylePickedName As String$/m, 'the picked display name must be shared with the form');
  assert.match(bas, /^Public gStyleCurrent As String$/m, 'the current style must be shared with the form');
});

test('the Word build script injects the style picker form', () => {
  const build = read(BUILD_WORD);
  assert.ok(build.includes('PWStylePicker.code.bas'), 'the build must inject the style form module');
  assert.ok(build.includes('"PWStylePicker"'), 'the form must be named PWStylePicker');
  assert.ok(build.includes('$styleForm.Properties.Item("Width").Value = 460'),
    'the style dialog must be 460 px wide');
  assert.ok(build.includes('$styleForm.Properties.Item("Height").Value = 420'),
    'the style dialog must be 420 px tall');
  assert.ok(build.includes('$styleForm.Properties.Item("StartUpPosition").Value = 1'),
    'the style dialog must be centred');
  assert.ok((build.match(/VBComponents\.Add\(3\)/g) || []).length >= 2,
    'both UserForms must be created as type-3 components');
  assert.match(build, /Type -eq 3/, 'stale form components must still be removed first');
});

/* ============================================================
 * Word「添加引注」独立网页（2.7.36）：web/word-picker.*
 *   PWInsertCitation 用一次性 token 打开；选完 POST /api/word/pick
 *   数据：GET /api/word/categories.txt + /api/word/papers.txt
 * ============================================================ */
const WP_HTML = 'web/word-picker.html';
const WP_CSS = 'web/css/word-picker.css';
const WP_JS = 'web/js/word-picker.js';

test('the Word picker page ships with the expected shell', () => {
  assert.ok(exists(WP_HTML), 'web/word-picker.html must exist');
  const html = read(WP_HTML);
  assert.ok(html.includes('<title>添加引注 · NPL</title>'),
    'the page must be titled 添加引注 · NPL');
  for (const id of ['tree', 'rows', 'q', 'insert', 'cancel']) {
    assert.ok(html.includes(`id="${id}"`), `the page must expose #${id}`);
  }
  assert.ok(html.includes('href="/css/word-picker.css"'), 'the page must load its own stylesheet');
  assert.ok(html.includes('src="/js/word-picker.js"'), 'the page must load its own script');
  assert.ok(html.includes('charset="utf-8"'), 'the page must declare UTF-8');
});

test('the Word picker stylesheet draws white / pale-green zebra rows', () => {
  const tokens = read('web/css/tokens.css');
  const css = read(WP_CSS);
  // 2.7.42 起行色只在 tokens.css 定义，引注页用 --zebra/--select 做别名。
  assert.match(tokens, /--row-alt:\s*#e5f2ec/, 'the zebra colour must live in the shared --row-alt token');
  assert.match(tokens, /--row-selected:\s*#d7ece6/, 'the selected row must live in the shared --row-selected token');
  assert.match(css, /--zebra:\s*var\(--row-alt\)/, 'the picker must alias --row-alt as --zebra');
  assert.match(css, /--select:\s*var\(--row-selected\)/, 'the picker must alias --row-selected as --select');
  assert.ok(tokens.includes('--accent: #0e7c86'),
    'the primary accent must match the main app (--accent: #0e7c86)');
  assert.ok(tokens.includes('--accent-2: #14a3a3'),
    'the secondary accent must match the main app (--accent-2: #14a3a3)');
  assert.match(css, /\.wp-row:nth-child\(odd\)\s*\{\s*background:\s*#ffffff;?\s*\}/,
    'odd rows must be white');
  assert.match(css, /\.wp-row:nth-child\(even\)\s*\{\s*background:\s*var\(--zebra\);?\s*\}/,
    'even rows must use the zebra alias');
  assert.ok(css.includes('.wp-row.selected'), 'selected rows need their own state');
  const selected = css.slice(css.indexOf('.wp-row.selected'));
  assert.ok(selected.slice(0, 220).includes('box-shadow: inset 3px 0 0 var(--accent-2)'),
    'the selected row must carry a 3px left accent bar');
  assert.equal(selected.slice(0, 220).includes('!important'), false,
    'the selected row must win by rule order, not !important');
});

test('the Word picker script talks to the documented endpoints with the URL token', () => {
  const js = read(WP_JS);
  assert.ok(js.includes("new URLSearchParams(location.search).get('token')"),
    'the one-time token must be read from the URL query');
  assert.ok(js.includes("'/api/word/categories.txt'"), 'the tree must come from categories.txt');
  assert.ok(js.includes("'/api/word/papers.txt?q='"), 'the rows must come from papers.txt');
  assert.ok(js.includes("'/api/word/pick'"), 'the result must be posted to /api/word/pick');
  assert.ok(js.includes("method: 'POST'"), 'the result submission must be a POST');
  assert.ok(js.includes('body: JSON.stringify({ token: token, ids: ids })'),
    'the POST body must carry the token and the chosen ids');
  assert.ok(js.includes('navigator.sendBeacon'),
    'closing the tab must still report a cancel through sendBeacon');
  assert.ok(js.includes('limit=500'), 'the row request must keep the 500-row cap');
});

test('the Word picker script supports ctrl/shift multi-select, dblclick and keys', () => {
  const js = read(WP_JS);
  assert.ok(js.includes('ev.shiftKey') && js.includes('ev.ctrlKey || ev.metaKey'),
    'click selection must support Shift ranges and Ctrl/Cmd toggles');
  assert.ok(js.includes("addEventListener('dblclick'"), 'double-click must insert immediately');
  assert.ok(js.includes("ev.key === 'Escape'"), 'Escape must cancel');
  assert.ok(js.includes("ev.key === 'Enter'"), 'Enter must insert');
  assert.ok(js.includes('state.selected.add(row.id)') && js.includes('state.selected.delete(row.id)'),
    'the selection must track paper ids, not row indices');
  assert.ok(js.includes('if (!token) { flash('),
    'a missing token must be reported instead of posting to the service');
});


