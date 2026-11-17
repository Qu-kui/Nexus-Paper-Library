Attribute VB_Name = "PaperWhale"
Option Explicit

#If VBA7 Then
Private Declare PtrSafe Sub Sleep Lib "kernel32" (ByVal dwMilliseconds As Long)
#Else
Private Declare Sub Sleep Lib "kernel32" (ByVal dwMilliseconds As Long)
#End If

' ============================================================
' NPL Word 插件（像 Zotero 一样插入引用）
' 依赖：本机 NPL 桌面端（默认 http://127.0.0.1:8765）
'
' 数据模型：
'   每条引文 = 书签 PWC_<n> + 文档变量 PW_IDS_<n>（存 paper_id，多个用逗号分隔）
'   文末参考文献 = 书签 PW_BIBLIOGRAPHY；引文格式 = 文档变量 PW_STYLE
' 刷新时按书签在文档里的先后顺序编号，所以 ieee / nature / gbt7714 的 [1][2]
' 与参考文献列表顺序永远一致。
' ============================================================

Private Const PW_APP As String = "NPL"
Private Const PW_SETTINGS_APP As String = "PaperWhale"
Private Const PW_DEFAULT_PORT As String = "8765"
Private Const PWC_PREFIX As String = "PWC_"
Private Const PWC_VAR_PREFIX As String = "PW_IDS_"
Private Const BIB_BOOKMARK As String = "PW_BIBLIOGRAPHY"
Private Const BIB_TITLE As String = "参考文献"
Private Const STYLE_VAR As String = "PW_STYLE"
Private Const CITE_PLACEHOLDER As String = "[…]"

Public gRibbonReady As Boolean
Private gLastError As String
Private gSilent As Boolean
Private gBase As String
Private gNumericStyle As Long
Private gNumStyleKey As String
Private gNumStyleVal As Long

' 引文选择窗口（PWCitationPicker）与标准模块之间的数据传递：
'   gPickerQuery = 打开窗口时的预填检索词（文档选中文字）
'   gPickerIds   = 用户在窗口里选中的 paper_id，逗号分隔；空串表示取消
Public gPickerQuery As String
Public gPickerIds As String
Public gPickerRows As String
Public gPickToken As String
Public gStyleCurrent As String
Public gStylePicked As String
Public gStylePickedName As String

' 自动化/命令行调用时可关掉弹窗，只靠返回值判断
Public Sub PWSetSilent(ByVal flag As Boolean)
    gSilent = flag
End Sub

Private Sub PWError(message As String)
    If Not gSilent Then MsgBox message, vbExclamation, PW_APP
End Sub


' ---------- 功能区回调（customUI.xml 的 onLoad / onAction 指向这里） ----------

Public Sub PWOnLoad(ribbon As Object)
    gRibbonReady = True
End Sub

Public Sub PWBtnInsertCitation(control As Object)
    PWInsertCitation
End Sub

Public Sub PWBtnInsertBibliography(control As Object)
    PWInsertBibliography
End Sub

Public Sub PWBtnRefresh(control As Object)
    PWRefreshCitations
End Sub

Public Sub PWBtnStyle(control As Object)
    PWSetStyle
End Sub

Public Sub PWBtnPort(control As Object)
    PWSetPort
End Sub

Public Sub PWBtnTest(control As Object)
    PWTestConnection
End Sub

Public Sub PWBtnHelp(control As Object)
    PWShowHelp
End Sub


' ---------- 插入引文 ----------

' 打开 Zotero 式的「添加引注」独立窗口（浏览器渲染），选完回传 id 后插入
Public Sub PWInsertCitation()
    Dim ids As String, token As String, url As String, raw As String
    Dim t0 As Double, waitSec As Double, skipBrowser As Boolean

    If Not PWRequireDocument() Then Exit Sub

    token = PWNewToken()
    gPickToken = token
    gPickerIds = ""
    PWSetDocVar "PW_PICK_TOKEN", token

    skipBrowser = (PWGetDocVar("PW_SKIP_BROWSER") = "1")
    If Not skipBrowser Then
        url = PWBase() & "/word-picker?token=" & token
        On Error Resume Next
        ActiveDocument.FollowHyperlink Address:=url
        If Err.Number <> 0 Then
            Err.Clear
            On Error GoTo 0
            PWError "打开引文选择窗口失败。请确认默认浏览器可用。"
            Exit Sub
        End If
        On Error GoTo 0
    End If

    ' 轮询浏览器回传（DoEvents 保持 Word 消息循环，超时 240 秒）
    t0 = Timer
    waitSec = 240
    Do While True
        DoEvents
        raw = PWGet("/api/word/pick?token=" & token)
        If Left$(raw, 4) <> "ERR:" Then
            raw = Trim$(raw)
            If raw = "__CANCELLED__" Then Exit Sub
            If Len(raw) > 0 Then
                gPickerIds = raw
                Exit Do
            End If
        End If
        PWSleepMs 150
        If Timer < t0 Then t0 = t0 - 86400
        If Timer - t0 > waitSec Then Exit Do
    Loop

    ids = gPickerIds
    gPickerIds = ""
    If Len(ids) = 0 Then Exit Sub

    PWInsertCitationGroup ids
    If PWRefreshAll(False) < 0 Then
        PWError "引文已插入，但刷新文字失败：" & gLastError
    End If
End Sub

Private Sub PWSleepMs(ByVal ms As Long)
    On Error Resume Next
    Sleep ms
End Sub

' 一次性 token：时间戳 + 随机数，浏览器回传/Word 轮询用
Private Function PWNewToken() As String
    Dim fixed As String

    ' 自动化测试用：文档变量 PW_PICK_TOKEN_OVERRIDE 可指定固定 token
    fixed = PWGetDocVar("PW_PICK_TOKEN_OVERRIDE")
    If Len(fixed) > 0 Then
        PWNewToken = fixed
        Exit Function
    End If
    Randomize
    PWNewToken = Format$(Now, "yyyymmddhhnnss") & CStr(Int(Rnd() * 1000000))
End Function

Private Sub PWInsertCitationGroup(ids As String)
    Dim n As Long, bmName As String, startPos As Long, delta As Long
    Dim rng As Range
    Dim bm As Bookmark, protectNames() As String, protectCount As Long, k As Long

    n = PWNextCitationNumber()
    bmName = PWC_PREFIX & CStr(n)
    PWSetDocVar PWC_VAR_PREFIX & CStr(n), ids

    ' 在已有引文的「开头」插入新引文时，Word 会把旧书签的起点留在原位（书签被撑大），
    ' 刷新时位置就错了。这里先把「正好从插入点开始」的旧书签记下来，插入后再挪到新位置。
    startPos = Selection.Start
    protectCount = 0
    For Each bm In ActiveDocument.Bookmarks
        If Left$(bm.Name, Len(PWC_PREFIX)) = PWC_PREFIX Then
            If bm.Range.Start = startPos Then
                ReDim Preserve protectNames(protectCount)
                protectNames(protectCount) = bm.Name
                protectCount = protectCount + 1
            End If
        End If
    Next bm

    Selection.TypeText Text:=CITE_PLACEHOLDER
    startPos = Selection.Start - Len(CITE_PLACEHOLDER)
    Set rng = ActiveDocument.Range(startPos, startPos + Len(CITE_PLACEHOLDER))
    ActiveDocument.Bookmarks.Add Name:=bmName, Range:=rng
    ' 引文后面补一个空格：既符合排版，也让相邻两条引文的书签边界不重叠
    Selection.Collapse Direction:=0
    Selection.TypeText Text:=" "

    delta = Len(CITE_PLACEHOLDER) + 1
    For k = 0 To protectCount - 1
        On Error Resume Next
        Set bm = ActiveDocument.Bookmarks(protectNames(k))
        If Not bm Is Nothing Then
            If bm.Range.Start = startPos Then
                ' 被撑大了：起点没动，内容整体右移 delta，重新挂一次书签
                Set rng = ActiveDocument.Range(startPos + delta, bm.Range.End)
                bm.Delete
                ActiveDocument.Bookmarks.Add Name:=protectNames(k), Range:=rng
            End If
        End If
        On Error GoTo 0
        Set bm = Nothing
    Next k
End Sub

Private Function PWNextCitationNumber() As Long
    Dim bm As Bookmark, maxN As Long, suffix As Long
    For Each bm In ActiveDocument.Bookmarks
        If Left$(bm.Name, Len(PWC_PREFIX)) = PWC_PREFIX Then
            suffix = Val(Mid$(bm.Name, Len(PWC_PREFIX) + 1))
            If suffix > maxN Then maxN = suffix
        End If
    Next bm
    PWNextCitationNumber = maxN + 1
End Function


' ---------- 参考文献 ----------

Public Sub PWInsertBibliography()
    Dim bib As Bookmark, startPos As Long
    Dim rng As Range

    If Not PWRequireDocument() Then Exit Sub
    If PWCitationCount() = 0 Then
        MsgBox "文档里还没有 NPL 引文。先用「插入引文」。", vbInformation, PW_APP
        Exit Sub
    End If

    On Error Resume Next
    Set bib = ActiveDocument.Bookmarks(BIB_BOOKMARK)
    On Error GoTo 0

    If bib Is Nothing Then
        ' 第一次：放到文末，先只写标题，正文由刷新填
        Selection.EndKey Unit:=wdStory
        Selection.TypeParagraph
        Selection.TypeParagraph
        startPos = Selection.Start
        Selection.TypeText Text:=BIB_TITLE
        Set rng = ActiveDocument.Range(startPos, startPos + Len(BIB_TITLE))
        ActiveDocument.Bookmarks.Add Name:=BIB_BOOKMARK, Range:=rng
    End If

    If PWRefreshAll(True) < 0 Then
        PWError "生成参考文献失败：" & gLastError
    End If
End Sub

' 更新文末参考文献（原地替换）；返回 False 表示失败，原因进 gLastError
Private Function PWRefreshBibliography(bibText As String) As Boolean
    Dim bib As Bookmark, startPos As Long, content As String, oldLen As Long, endPos As Long
    Dim rng As Range
    Dim stage As String

    stage = "locate"
    On Error GoTo EH
    On Error Resume Next
    Set bib = ActiveDocument.Bookmarks(BIB_BOOKMARK)
    On Error GoTo EH
    If bib Is Nothing Then
        PWRefreshBibliography = True
        Exit Function
    End If

    content = BIB_TITLE & vbCrLf & vbCrLf & Replace(bibText, vbLf, vbCrLf)
    startPos = bib.Range.Start
    oldLen = bib.Range.End - bib.Range.Start
    stage = "delete old"
    bib.Delete
    stage = "write text"
    Set rng = ActiveDocument.Range(startPos, startPos + oldLen)
    rng.Text = content
    ' Word 会把 vbCrLf 折成单个段落标记，所以用 Range.End 取真实结尾，不能拿 Len(content) 算
    endPos = rng.End
    stage = "add bookmark"
    Set rng = ActiveDocument.Range(startPos, endPos)
    rng.Bookmarks.Add Name:=BIB_BOOKMARK, Range:=rng
    stage = "format title"
    Set rng = ActiveDocument.Range(startPos, startPos + Len(BIB_TITLE))
    rng.Font.Bold = True
    rng.Font.Size = 14
    If Not ActiveDocument.Bookmarks.Exists(BIB_BOOKMARK) Then
        gLastError = "bib bookmark missing after refresh"
    End If
    PWRefreshBibliography = True
    Exit Function
EH:
    gLastError = "bib/" & stage & ": " & Err.Description
    PWRefreshBibliography = False
End Function


' ---------- 刷新 ----------

Public Sub PWRefreshCitations()
    Dim count As Long

    If Not PWRequireDocument() Then Exit Sub
    If PWCitationCount() = 0 Then
        MsgBox "文档里还没有 NPL 引文。", vbInformation, PW_APP
        Exit Sub
    End If
    count = PWRefreshAll(True)
    If count < 0 Then
        PWError "刷新失败：" & gLastError & vbCrLf & vbCrLf & "请确认 NPL 正在运行。"
    Else
        MsgBox "已刷新 " & count & " 处引文。", vbInformation, PW_APP
    End If
End Sub

' 刷新全部引文文字（并更新参考文献，如果文档里有）；成功返回引文数，失败返回 -1
Private Function PWRefreshAll(updateBibliography As Boolean) As Long
    Dim names() As String, starts() As Long, lens() As Long, idsList() As String
    Dim bm As Bookmark, count As Long, i As Long, j As Long
    Dim ordered As String, style As String, bibText As String
    Dim map As Object, rng As Range, newText As String, total As Long
    Dim tmpName As String, tmpStart As Long, tmpIds As String
    Dim parts() As String, k As Long, pid As String
    Dim stage As String
    Dim bStart As Long, bLen As Long

    stage = "start"
    On Error GoTo EH

    count = PWCitationCount()
    If count = 0 Then
        PWRefreshAll = 0
        Exit Function
    End If

    ReDim names(1 To count)
    ReDim starts(1 To count)
    ReDim lens(1 To count)
    ReDim idsList(1 To count)
    i = 0
    For Each bm In ActiveDocument.Bookmarks
        If Left$(bm.Name, Len(PWC_PREFIX)) = PWC_PREFIX Then
            i = i + 1
            names(i) = bm.Name
            starts(i) = bm.Range.Start
            lens(i) = bm.Range.End - bm.Range.Start
            idsList(i) = PWGetDocVar(PWC_VAR_PREFIX & Mid$(bm.Name, Len(PWC_PREFIX) + 1))
        End If
    Next bm

    ' 按文档位置排序（数量很小，冒泡即可）
    For i = 1 To count - 1
        For j = 1 To count - i
            If starts(j) > starts(j + 1) Then
                tmpStart = starts(j): starts(j) = starts(j + 1): starts(j + 1) = tmpStart
                tmpStart = lens(j): lens(j) = lens(j + 1): lens(j + 1) = tmpStart
                tmpName = names(j): names(j) = names(j + 1): names(j + 1) = tmpName
                tmpIds = idsList(j): idsList(j) = idsList(j + 1): idsList(j + 1) = tmpIds
            End If
        Next j
    Next i

    ' 按出现顺序收集唯一 id
    For i = 1 To count
        parts = Split(idsList(i), ",")
        For k = 0 To UBound(parts)
            pid = Trim$(parts(k))
            If Len(pid) > 0 Then
                If InStr("," & ordered & ",", "," & pid & ",") = 0 Then
                    If Len(ordered) > 0 Then ordered = ordered & ","
                    ordered = ordered & pid
                End If
            End If
        Next k
    Next i
    If Len(ordered) = 0 Then
        PWRefreshAll = 0
        Exit Function
    End If

    stage = "endpoint"
    style = PWStyle()
    Set map = PWDocumentReply(ordered, style, bibText)
    If map Is Nothing Then
        PWRefreshAll = -1
        Exit Function
    End If

    ' 从后往前替换，避免影响后面书签的位置
    stage = "replace"
    gNumericStyle = PWIsNumericStyle(style)
    total = 0
    For i = count To 1 Step -1
        newText = PWBuildCitationText(idsList(i), map)
        If Len(newText) > 0 Then
            stage = "replace " & names(i) & " ids=[" & idsList(i) & "]"
            Set bm = ActiveDocument.Bookmarks(names(i))
            bStart = bm.Range.Start
            bLen = bm.Range.End - bm.Range.Start
            bm.Delete
            ActiveDocument.Range(bStart, bStart + bLen).Text = newText
            ActiveDocument.Bookmarks.Add Name:=names(i), Range:=ActiveDocument.Range(bStart, bStart + Len(newText))
            total = total + 1
        End If
    Next i

    If updateBibliography Then
        If Not PWRefreshBibliography(bibText) Then
            PWRefreshAll = -1
            Exit Function
        End If
    Else
        PWEnsureBibliographyUpToDate bibText
    End If

    PWRefreshAll = total
    Exit Function
EH:
    gLastError = "[" & stage & "] " & Err.Description
    PWRefreshAll = -1
End Function

' 只有文档里已经存在参考文献时才顺手更新它
Private Sub PWEnsureBibliographyUpToDate(bibText As String)
    Dim bib As Bookmark
    On Error Resume Next
    Set bib = ActiveDocument.Bookmarks(BIB_BOOKMARK)
    On Error GoTo 0
    If Not bib Is Nothing Then
        If Not PWRefreshBibliography(bibText) Then
            ' 顺手更新失败不阻断引文刷新，错误留给 PWLastError 查
        End If
    End If
End Sub

Private Function PWBuildCitationText(ids As String, map As Object) As String
    Dim parts() As String, i As Long, pid As String, entry As String
    Dim one As String, idx As Long
    Dim nums() As Long, numCount As Long
    Dim inner As String, allBracket As Boolean, allParen As Boolean
    Dim first As Boolean

    parts = Split(ids, ",")
    ReDim nums(0 To UBound(parts) + 1)
    numCount = 0
    allBracket = True
    allParen = True
    first = True
    For i = 0 To UBound(parts)
        pid = Trim$(parts(i))
        If Len(pid) > 0 Then
            entry = ""
            On Error Resume Next
            entry = CStr(map(pid))
            On Error GoTo 0
            If Len(entry) > 0 Then
                idx = PWEntryIndex(entry)
                one = PWEntryInline(entry)
                If gNumericStyle Then
                    ' 数字型：只按文档里的编号折叠，[1] / [1,2] / [1-3]
                    If idx > 0 Then
                        nums(numCount) = idx
                        numCount = numCount + 1
                    End If
                ElseIf Len(one) > 0 Then
                    If Left$(one, 1) <> "[" Then allBracket = False
                    If Right$(one, 1) <> "]" Then allBracket = False
                    If Left$(one, 1) <> "(" Then allParen = False
                    If Right$(one, 1) <> ")" Then allParen = False
                    If first Then
                        inner = one
                        first = False
                    Else
                        If allParen Then
                            inner = inner & "; " & one
                        ElseIf allBracket Then
                            inner = inner & ", " & one
                        Else
                            inner = inner & "; " & one
                        End If
                    End If
                End If
            End If
        End If
    Next i

    If gNumericStyle Then
        If numCount > 0 Then PWBuildCitationText = PWNumericBracket(nums, numCount)
        Exit Function
    End If
    If Len(inner) = 0 Then Exit Function

    If UBound(parts) > 0 Then
        If allBracket Then
            PWBuildCitationText = "[" & Replace(Mid$(inner, 2, Len(inner) - 2), "][", ", ") & "]"
            Exit Function
        ElseIf allParen Then
            PWBuildCitationText = "(" & Replace(Mid$(inner, 2, Len(inner) - 2), ")(", "; ") & ")"
            Exit Function
        End If
    End If
    PWBuildCitationText = inner
End Function

' map 值格式：<index> <Chr(1)> <行内引文>
Private Function PWEntryIndex(ByVal entry As String) As Long
    Dim pos As Long
    pos = InStr(entry, Chr(1))
    If pos > 1 Then PWEntryIndex = Val(Left$(entry, pos - 1))
End Function

Private Function PWEntryInline(ByVal entry As String) As String
    Dim pos As Long
    pos = InStr(entry, Chr(1))
    If pos > 0 Then PWEntryInline = Mid$(entry, pos + 1)
End Function

' 数字型引文折叠：[1]、[1,2]、[1-3]、[1-3,5]
Private Function PWNumericBracket(nums() As Long, ByVal count As Long) As String
    Dim i As Long, j As Long, tmp As Long, runStart As Long, runLen As Long
    Dim out As String, k As Long

    ' 升序（组内一般已递增，这里保险）
    For i = 0 To count - 2
        For j = 0 To count - 2 - (i - 0)
            If nums(j) > nums(j + 1) Then
                tmp = nums(j)
                nums(j) = nums(j + 1)
                nums(j + 1) = tmp
            End If
        Next j
    Next i

    i = 0
    Do While i < count
        runStart = nums(i)
        runLen = 1
        Do While i + runLen < count
            If nums(i + runLen) = runStart + runLen Then
                runLen = runLen + 1
            Else
                Exit Do
            End If
        Loop
        If runLen >= 3 Then
            If Len(out) > 0 Then out = out & ","
            out = out & CStr(runStart) & "-" & CStr(runStart + runLen - 1)
        Else
            For k = 0 To runLen - 1
                If Len(out) > 0 Then out = out & ","
                out = out & CStr(runStart + k)
            Next k
        End If
        i = i + runLen
    Loop
    PWNumericBracket = "[" & out & "]"
End Function

' 是否数字型格式（吃 styles.txt 第三列，1 = 数字型）
Private Function PWIsNumericStyle(ByVal style As String) As Boolean
    Dim raw As String, lines() As String, parts() As String, i As Long

    If gNumStyleKey <> style Then
        gNumStyleKey = style
        gNumStyleVal = 0
        raw = PWGet("/api/cite/styles.txt")
        If Left$(raw, 4) <> "ERR:" Then
            lines = Split(Replace(raw, vbCrLf, vbLf), vbLf)
            For i = 0 To UBound(lines)
                parts = Split(lines(i), vbTab)
                If UBound(parts) >= 1 Then
                    If parts(0) = style Then
                        If UBound(parts) >= 2 Then gNumStyleVal = Val(parts(2))
                    End If
                End If
            Next i
        End If
    End If
    PWIsNumericStyle = (gNumStyleVal = 1)
End Function


' ---------- 设置 ----------

' 从 NPL 拉当前可用格式（内置 6 种 + 自定义/导入的），在可搜索的列表里选
Public Sub PWSetStyle()
    gStyleCurrent = PWStyle()
    gStylePicked = ""
    gStylePickedName = ""

    On Error GoTo EH
    PWStylePicker.Show vbModal
    On Error GoTo 0

    If Len(gStylePicked) = 0 Then Exit Sub

    PWSetDocVar STYLE_VAR, gStylePicked
    SaveSetting PW_SETTINGS_APP, "Config", "Style", gStylePicked
    If PWCitationCount() > 0 Then PWRefreshAll True
    MsgBox "引文格式已设为：" & gStylePickedName, vbInformation, PW_APP
    Exit Sub

EH:
    On Error GoTo 0
    PWError "打开引文格式窗口失败：" & Err.Description
End Sub

Private Function PWStyleIndex(ids() As String, count As Long, current As String) As Long
    Dim i As Long
    For i = 0 To count - 1
        If ids(i) = current Then
            PWStyleIndex = i + 1
            Exit Function
        End If
    Next i
    PWStyleIndex = 1
End Function

Public Sub PWSetPort()
    Dim value As String
    value = Trim$(InputBox("NPL 本机端口（默认 8765）：", "NPL 设置", PWPort()))
    If Len(value) = 0 Then Exit Sub
    If Val(value) < 1 Or Val(value) > 65535 Then
        MsgBox "端口不合法。", vbExclamation, PW_APP
        Exit Sub
    End If
    SaveSetting PW_SETTINGS_APP, "Config", "Port", CStr(Val(value))
    gBase = ""
    MsgBox "已保存端口：" & Val(value) & vbCrLf & "（重启 Word 后生效）", vbInformation, PW_APP
End Sub

Public Sub PWTestConnection()
    Dim raw As String, version As String
    raw = PWGet("/api/health")
    If Left$(raw, 4) = "ERR:" Then
        MsgBox "连接失败：" & Mid$(raw, 6) & vbCrLf & vbCrLf & "请先启动 NPL 桌面程序。", vbExclamation, PW_APP
        Exit Sub
    End If
    version = PWJsonValue(raw, "version")
    MsgBox "连接成功。" & vbCrLf & "NPL 版本：" & version & vbCrLf & "地址：" & PWBase(), vbInformation, PW_APP
End Sub

Public Sub PWShowHelp()
    MsgBox _
        "NPL Word 插件用法（2.7.56）：" & vbCrLf & vbCrLf & _
        "0. 先启动 NPL 桌面程序（默认端口 8765）；装完或升级后请重启一次 Word。" & vbCrLf & _
        "1. 「插入引文」：打开独立的「添加引注」窗口——左侧分类树、顶部搜索、右侧条目表；滚轮滚动，Ctrl/Shift 多选，双击某行直接插入，Enter 插入，Esc 取消。选完点「插入引文」，窗口关闭并自动插入、重排编号。" & vbCrLf & _
        "2. 数字型格式（IEEE / Nature / GB-T 7714，或自定义的数字型）：单篇 [1]，两篇 [1,2]，连续三篇以上折叠成 [1-3]；在已有引文前面插入时，后面的编号会自动顺延。" & vbCrLf & _
        "3. 「插入 / 更新参考文献」：第一次放到文末，之后点它或「刷新」都会原地更新，不会重复追加。" & vbCrLf & _
        "4. 「引文格式」：可搜索的格式列表，内置 APA / MLA / Chicago / IEEE / Nature / GB/T 7714，以及设置页里用一句话生成或导入 .csl 的自定义格式；数字型会标「（数字型）」。" & vbCrLf & _
        "5. 「刷新」：库里改了标题/年份/卷期页，或手动挪动过引文位置时点，按文档顺序重排。" & vbCrLf & vbCrLf & _
        "引文以书签保存，别手动删掉引文文字；删了就重新插入一次。" & vbCrLf & _
        "更多用法（文献导入、AI 精读、方法与参数库、复习卡片、浏览器插件等）见程序目录 docs\使用说明.txt。", _
        vbInformation, PW_APP & " 使用说明"
End Sub


' ---------- 对外测试入口 ----------

Public Function PWHealth() As String
    PWHealth = PWGet("/api/health")
End Function

Public Function PWRibbonReady() As Boolean
    PWRibbonReady = gRibbonReady
End Function

Public Function PWLastError() As String
    PWLastError = gLastError
End Function

Public Function PWSearchText(ByVal q As String) As String
    PWSearchText = PWPost("/api/cite/search.txt", "{""q"":""" & PWEscape(q) & """,""limit"":10}")
End Function

Public Function PWCiteCount() As Long
    PWCiteCount = PWCitationCount()
End Function

' 直接按 paper_id 插入（给命令行 / 自动化用，跳过检索框）
Public Sub PWInsertCitationById(ByVal paperId As String)
    If Not PWRequireDocument() Then Exit Sub
    If Len(Trim$(paperId)) = 0 Then Exit Sub
    PWInsertCitationGroup Trim$(paperId)
    If PWRefreshAll(False) < 0 Then
        PWError "引文已插入，但刷新文字失败：" & gLastError
    End If
End Sub

' 自动化友好：从文档变量 PW_TEST_ID 取 id 再插入（避开 COM 传参的兼容问题）
Public Sub PWInsertCitationFromVar()
    Dim pid As String
    pid = PWGetDocVar("PW_TEST_ID")
    If Len(pid) = 0 Then Exit Sub
    PWInsertCitationById pid
End Sub

' 自动化友好：加载「插入引文」窗口的数据、按 PW_TEST_ROWS 选行（1 开始）、取 ids 直接插入。
' 不显示窗口，用来验证「列表 → 多选 → 插入」整条链路。
Public Sub PWTestPickerInsert()
    Dim ids As String, f As Object, catIdx As Long, catRows As Long

    If Not PWRequireDocument() Then Exit Sub
    gPickerQuery = ""
    gPickerIds = ""
    gPickerRows = PWGetDocVar("PW_TEST_ROWS")
    If Len(gPickerRows) = 0 Then gPickerRows = "1"

    On Error GoTo EH
    Set f = VBA.UserForms.Add("PWCitationPicker")
    catIdx = Val(PWGetDocVar("PW_TEST_CATEGORY"))
    If catIdx > 0 Then catRows = f.PWTestSelectCategory(catIdx)
    ids = f.PWTestPickRows()
    Unload f
    Err.Clear
    On Error GoTo 0

    If Len(ids) = 0 Then
        PWError "选择窗口没有返回文献（PW_TEST_ROWS=" & gPickerRows & "）。"
        Exit Sub
    End If
    PWInsertCitationGroup ids
    If PWRefreshAll(False) < 0 Then
        PWError "引文已插入，但刷新文字失败：" & gLastError
    End If
    Exit Sub

EH:
    On Error GoTo 0
    PWError "加载引文窗口失败：" & Err.Description
End Sub

' 不弹窗的刷新（自动化用）；返回刷新数量，失败 -1
Public Function PWRefreshSilent() As Long
    PWRefreshSilent = PWRefreshAll(True)
End Function

' 诊断：返回本机地址与每个 HTTP 通道的探测结果（自动化 / 排障用）
Public Function PWDebugHttp() As String
    Dim providers As Variant, i As Long, out As String
    providers = Array( _
        "MSXML2.ServerXMLHTTP.6.0", _
        "MSXML2.XMLHTTP.6.0", _
        "MSXML2.XMLHTTP", _
        "WinHttp.WinHttpRequest.5.1")
    out = "base=" & PWBase() & vbCrLf
    For i = LBound(providers) To UBound(providers)
        out = out & CStr(providers(i)) & " => " & PWHttpOnce(CStr(providers(i)), "GET", "/api/health", "") & vbCrLf
    Next i
    PWDebugHttp = out
End Function


' ---------- 文档状态 ----------

Private Function PWRequireDocument() As Boolean
    Dim doc As Document
    On Error Resume Next
    Set doc = ActiveDocument
    On Error GoTo 0
    If doc Is Nothing Then
        MsgBox "请先打开一个 Word 文档。", vbExclamation, PW_APP
        Exit Function
    End If
    PWRequireDocument = True
End Function

Private Function PWCitationCount() As Long
    Dim bm As Bookmark, count As Long
    For Each bm In ActiveDocument.Bookmarks
        If Left$(bm.Name, Len(PWC_PREFIX)) = PWC_PREFIX Then count = count + 1
    Next bm
    PWCitationCount = count
End Function

' 选中文字（单行、不太长）作为检索词
Private Function PWSelectionQuery() As String
    Dim text As String
    On Error Resume Next
    text = Selection.Text
    On Error GoTo 0
    text = Replace(text, Chr(13), " ")
    text = Replace(text, Chr(10), " ")
    text = Replace(text, Chr(7), " ")
    text = Trim$(text)
    If Len(text) > 120 Then text = ""
    PWSelectionQuery = text
End Function

Private Function PWGetDocVar(name As String) As String
    On Error Resume Next
    PWGetDocVar = ActiveDocument.Variables(name).Value
    On Error GoTo 0
End Function

Private Sub PWSetDocVar(name As String, value As String)
    On Error Resume Next
    ActiveDocument.Variables(name).Value = value
    If Err.Number <> 0 Then
        Err.Clear
        ActiveDocument.Variables.Add name, value
    End If
    On Error GoTo 0
End Sub


' ---------- 后端文档接口解析 ----------

' 返回 id → 引文文字 的字典；bibOut 出参考文献正文；失败返回 Nothing
Private Function PWDocumentReply(orderedIds As String, style As String, ByRef bibOut As String) As Object
    Dim raw As String, lines() As String, i As Long, line As String
    Dim tabPos As Long, inBib As Boolean
    Dim bibParts() As String, bibCount As Long
    Dim map As Object

    gLastError = ""
    Set map = CreateObject("Scripting.Dictionary")
    raw = PWPost("/api/cite/document.txt", "{""ids"":[" & PWJsonStringArray(orderedIds) & "],""style"":""" & style & """}")
    If Left$(raw, 4) = "ERR:" Then
        gLastError = Mid$(raw, 6)
        Set PWDocumentReply = Nothing
        Exit Function
    End If

    bibCount = 0
    inBib = False
    lines = Split(Replace(raw, vbCrLf, vbLf), vbLf)
    For i = 0 To UBound(lines)
        line = lines(i)
        If line = "--BIBLIOGRAPHY--" Then
            inBib = True
        ElseIf inBib Then
            ReDim Preserve bibParts(0 To bibCount)
            bibParts(bibCount) = line
            bibCount = bibCount + 1
        Else
            tabPos = InStr(line, vbTab)
            If tabPos > 1 Then
                ' 三列：id <TAB> index <TAB> 引文文字（旧版两列也能解析）
                Dim rest As String, tab2 As Long, idx As Long, body As String
                rest = Mid$(line, tabPos + 1)
                tab2 = InStr(rest, vbTab)
                If tab2 > 0 Then
                    idx = Val(Left$(rest, tab2 - 1))
                    body = Mid$(rest, tab2 + 1)
                Else
                    idx = 0
                    body = rest
                End If
                map(Left$(line, tabPos - 1)) = idx & Chr(1) & body
            End If
        End If
    Next i
    If bibCount > 0 Then bibOut = Join(bibParts, vbLf)
    Set PWDocumentReply = map
End Function

Private Function PWJsonStringArray(ids As String) As String
    Dim parts() As String, i As Long, out As String
    parts = Split(ids, ",")
    For i = 0 To UBound(parts)
        If Len(Trim$(parts(i))) > 0 Then
            If Len(out) > 0 Then out = out & ","
            out = out & """" & PWEscape(Trim$(parts(i))) & """"
        End If
    Next i
    PWJsonStringArray = out
End Function


' ---------- 配置 ----------

Private Function PWPort() As String
    Dim value As String
    On Error Resume Next
    value = GetSetting(PW_SETTINGS_APP, "Config", "Port", PW_DEFAULT_PORT)
    On Error GoTo 0
    If Len(Trim$(value)) = 0 Then value = PW_DEFAULT_PORT
    PWPort = Trim$(value)
End Function

Private Function PWBase() As String
    If Len(gBase) = 0 Then gBase = PWDetectBase()
    PWBase = gBase
End Function

' 端口可能被改过、也可能是别的实例留下的旧值：依次探测，能通的才用
Private Function PWDetectBase() As String
    Dim ports As Variant, i As Long, candidate As String, saved As String, url As String
    saved = PWPort()
    ports = Array(saved, "8765", "8797")   ' 8797 是本机调试实例常用的端口
    For i = LBound(ports) To UBound(ports)
        candidate = Trim$(CStr(ports(i)))
        If Len(candidate) > 0 Then
            url = "http://127.0.0.1:" & candidate
            If PWProbe(url & "/api/health") Then
                If candidate <> saved Then SaveSetting PW_SETTINGS_APP, "Config", "Port", candidate
                PWDetectBase = url
                Exit Function
            End If
        End If
    Next i
    ' 全都不通：用设置值，报错信息才贴近用户设置
    PWDetectBase = "http://127.0.0.1:" & saved
End Function

Private Function PWProbe(url As String) As Boolean
    Dim http As Object
    On Error Resume Next
    Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
    If http Is Nothing Then Set http = CreateObject("MSXML2.XMLHTTP.6.0")
    If http Is Nothing Then Exit Function
    http.setTimeouts 500, 500, 1000, 1500   ' 只有 ServerXMLHTTP 支持，失败无妨
    http.Open "GET", url, False
    http.send
    If Err.Number = 0 Then
        If http.Status = 200 Then PWProbe = True
    End If
    Err.Clear
    On Error GoTo 0
End Function

Private Function PWStyle() As String
    Dim value As String
    value = PWGetDocVar(STYLE_VAR)
    If Len(value) = 0 Then
        On Error Resume Next
        value = GetSetting(PW_SETTINGS_APP, "Config", "Style", "ieee")
        On Error GoTo 0
    End If
    If Len(value) = 0 Then value = "ieee"
    PWStyle = value
End Function


' ---------- HTTP / 工具 ----------

Public Function PWGet(path As String) As String
    PWGet = PWHttp("GET", path, "")
End Function

' 把检索词按 UTF-8 百分号编码（窗口里搜中文也要能传到后端）
Public Function PWUrlEncode(ByVal text As String) As String
    Dim bytes As Variant, i As Long, startIdx As Long, out As String, b As Long

    If Len(text) = 0 Then Exit Function
    bytes = PWToUtf8(text)
    startIdx = LBound(bytes)
    ' ADODB.Stream 写 UTF-8 时会在开头塞 3 字节 BOM，编码前先跳过
    If UBound(bytes) >= startIdx + 2 Then
        If bytes(startIdx) = 239 And bytes(startIdx + 1) = 187 And bytes(startIdx + 2) = 191 Then
            startIdx = startIdx + 3
        End If
    End If
    For i = startIdx To UBound(bytes)
        b = bytes(i)
        If (b >= 48 And b <= 57) Or (b >= 65 And b <= 90) Or (b >= 97 And b <= 122) _
           Or b = 45 Or b = 46 Or b = 95 Or b = 126 Then
            out = out & Chr$(b)
        Else
            out = out & "%" & Right$("0" & Hex$(b), 2)
        End If
    Next i
    PWUrlEncode = out
End Function

Private Function PWPost(path As String, body As String) As String
    PWPost = PWHttp("POST", path, body)
End Function

Private Function PWHttp(method As String, path As String, body As String) As String
    ' 依次尝试 WinHTTP / WinINET：有些机器上 ServerXMLHTTP 走代理连不上 127.0.0.1
    Dim providers As Variant, i As Long, result As String
    providers = Array( _
        "MSXML2.ServerXMLHTTP.6.0", _
        "MSXML2.XMLHTTP.6.0", _
        "MSXML2.XMLHTTP", _
        "WinHttp.WinHttpRequest.5.1")
    result = ""
    For i = LBound(providers) To UBound(providers)
        result = PWHttpOnce(CStr(providers(i)), method, path, body)
        If Left$(result, 4) <> "ERR:" Then
            PWHttp = result
            Exit Function
        End If
    Next i
    PWHttp = result
End Function

Private Function PWHttpOnce(provider As String, method As String, path As String, body As String) As String
    Dim http As Object
    On Error GoTo ErrHandler
    Set http = CreateObject(provider)
    On Error Resume Next
    http.setTimeouts 2000, 2000, 4000, 8000   ' 只有 ServerXMLHTTP 有；失败就忽略
    On Error GoTo ErrHandler
    http.Open method, PWBase() & path, False
    http.setRequestHeader "Content-Type", "application/json; charset=utf-8"
    If method = "POST" Then
        http.send PWToUtf8(body)
    Else
        http.send
    End If
    If http.Status < 200 Or http.Status >= 300 Then
        Err.Raise vbObjectError + 10, PW_APP, "HTTP " & http.Status
    End If
    PWHttpOnce = PWFromUtf8(http.responseBody)
    Exit Function
ErrHandler:
    PWHttpOnce = "ERR: " & Err.Description
End Function

Private Function PWToUtf8(text As String) As Variant
    Dim stream As Object
    Set stream = CreateObject("ADODB.Stream")
    stream.Type = 2
    stream.Charset = "utf-8"
    stream.Open
    stream.WriteText text
    stream.Position = 0
    stream.Type = 1
    stream.Position = 0
    PWToUtf8 = stream.Read
    stream.Close
End Function

Private Function PWFromUtf8(bytes As Variant) As String
    Dim stream As Object
    Set stream = CreateObject("ADODB.Stream")
    stream.Type = 1
    stream.Open
    stream.Write bytes
    stream.Position = 0
    stream.Type = 2
    stream.Charset = "utf-8"
    PWFromUtf8 = stream.ReadText
    stream.Close
End Function

Private Function PWEscape(text As String) As String
    Dim value As String
    value = Replace(text, Chr(92), Chr(92) & Chr(92))
    value = Replace(value, Chr(34), Chr(92) & Chr(34))
    value = Replace(value, Chr(13), " ")
    value = Replace(value, Chr(10), " ")
    PWEscape = value
End Function

Private Function PWJsonValue(raw As String, key As String) As String
    Dim pos As Long, rest As String, endPos As Long
    pos = InStr(raw, """" & key & """")
    If pos = 0 Then Exit Function
    rest = Mid$(raw, pos + Len(key) + 2)
    pos = InStr(rest, ":")
    If pos = 0 Then Exit Function
    rest = Trim$(Mid$(rest, pos + 1))
    If Left$(rest, 1) = """" Then
        rest = Mid$(rest, 2)
        endPos = InStr(rest, """")
        If endPos > 0 Then PWJsonValue = Left$(rest, endPos - 1)
    End If
End Function
