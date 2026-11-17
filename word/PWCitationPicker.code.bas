Option Explicit

' ============================================================
' 「添加引注」窗口（UserForm 名：PWCitationPicker）
'
' 版式参考 Zotero：顶部搜索 · 左侧分类 · 右侧条目表 · 底部状态与按钮。
' 尺寸按 Word 窗口可用空间自适应（高分屏 / 小笔记本上不裁底部按钮）。
'
' 数据来源：
'   GET /api/word/categories.txt   #stats 行 + depth/path/count
'   GET /api/word/papers.txt?q=&scope=&limit=500   列：id year authors title venue has_pdf
'
' 对外：gPickerQuery 预填检索词；gPickerIds 选中的 paper_id（逗号分隔）
' ============================================================

Private Const CAT_W As Single = 200
Private Const EDGE As Single = 12
Private Const AUTH_W As Single = 110
Private Const YEAR_W As Single = 45
Private Const VEN_W As Single = 127

Private WithEvents txtQuery As MSForms.TextBox
Private WithEvents btnSearch As MSForms.CommandButton
Private WithEvents btnAll As MSForms.CommandButton
Private WithEvents btnInsert As MSForms.CommandButton
Private WithEvents btnClose As MSForms.CommandButton
Private WithEvents lstCategories As MSForms.ListBox
Private WithEvents lstPapers As MSForms.ListBox
Private WithEvents labTip As MSForms.Label
Private WithEvents labStatus As MSForms.Label
Private labSearchTag As MSForms.Label
Private labCatTag As MSForms.Label
Private labHeadTitle As MSForms.Label
Private labHeadAuthors As MSForms.Label
Private labHeadYear As MSForms.Label
Private labHeadVenue As MSForms.Label
Private labDivider As MSForms.Label

Private mIds() As String
Private mScopes() As String
Private mCatCount As Long
Private mPaperX As Single
Private mPaperW As Single
Private mTitleW As Single
Private mPaneTop As Single
Private mPaneH As Single
Private mLoadingCats As Boolean

Public gPickerStage As String

Private Sub UserForm_Initialize()
    On Error GoTo EH
    gPickerStage = "caption"
    Me.Caption = "添加引注 · NPL"
    On Error Resume Next
    Me.Font.Name = "Microsoft YaHei"
    Me.Font.Size = 10
    On Error GoTo 0

    gPickerStage = "size"
    PWFitWindow

    gPickerStage = "search"
    Set labSearchTag = Me.Controls.Add("Forms.Label.1", "labSearchTag", True)
    labSearchTag.Caption = "搜索"
    labSearchTag.Height = 18
    labSearchTag.Width = 34

    Set txtQuery = Me.Controls.Add("Forms.TextBox.1", "txtQuery", True)
    txtQuery.Height = 22
    txtQuery.Value = gPickerQuery

    Set btnSearch = Me.Controls.Add("Forms.CommandButton.1", "btnSearch", True)
    btnSearch.Caption = "搜索"
    btnSearch.Height = 22
    btnSearch.Width = 64

    Set btnAll = Me.Controls.Add("Forms.CommandButton.1", "btnAll", True)
    btnAll.Caption = "全选"
    btnAll.Height = 22
    btnAll.Width = 80

    gPickerStage = "labels"
    Set labCatTag = Me.Controls.Add("Forms.Label.1", "labCatTag", True)
    labCatTag.Caption = "我的文库"
    labCatTag.Height = 14
    labCatTag.Font.Bold = True
    labCatTag.Font.Size = 8

    Set labHeadTitle = Me.Controls.Add("Forms.Label.1", "labHeadTitle", True)
    PWHeadStyle labHeadTitle, "标题"
    Set labHeadAuthors = Me.Controls.Add("Forms.Label.1", "labHeadAuthors", True)
    PWHeadStyle labHeadAuthors, "作者"
    Set labHeadYear = Me.Controls.Add("Forms.Label.1", "labHeadYear", True)
    PWHeadStyle labHeadYear, "年份"
    Set labHeadVenue = Me.Controls.Add("Forms.Label.1", "labHeadVenue", True)
    PWHeadStyle labHeadVenue, "期刊"

    Set labDivider = Me.Controls.Add("Forms.Label.1", "labDivider", True)
    labDivider.Caption = ""
    labDivider.Height = 1
    labDivider.BackColor = RGB(226, 232, 240)
    labDivider.BackStyle = 1

    Set labTip = Me.Controls.Add("Forms.Label.1", "labTip", True)
    labTip.Caption = ""
    Set labStatus = Me.Controls.Add("Forms.Label.1", "labStatus", True)
    labStatus.Caption = ""

    gPickerStage = "cats"
    Set lstCategories = Me.Controls.Add("Forms.ListBox.1", "lstCategories", True)
    With lstCategories
        .IntegralHeight = False
        .Font.Size = 9
    End With

    gPickerStage = "papers"
    Set lstPapers = Me.Controls.Add("Forms.ListBox.1", "lstPapers", True)
    With lstPapers
        .ColumnCount = 4
        .MultiSelect = 2
        .IntegralHeight = False
        .Font.Size = 9
    End With

    gPickerStage = "buttons"
    Set btnInsert = Me.Controls.Add("Forms.CommandButton.1", "btnInsert", True)
    With btnInsert
        .Caption = "插入引文"
        .Default = True
        .Height = 26
        .Width = 100
    End With
    Set btnClose = Me.Controls.Add("Forms.CommandButton.1", "btnClose", True)
    With btnClose
        .Caption = "取消"
        .Cancel = True
        .Height = 26
        .Width = 68
    End With

    gPickerStage = "layout"
    PWFitLayout
    PWApplyTheme
    PWLoadCategories
    PWLoadPapers gPickerQuery, PWCurrentScope()
    Exit Sub
EH:
    On Error Resume Next
    If Not labStatus Is Nothing Then
        labStatus.Caption = "窗口初始化失败（" & gPickerStage & "）：" & Err.Description
    End If
End Sub

' Show 时 MSForms 会把 ListBox 恢复成设计尺寸，所以窗口真正显示后再排一次
Private Sub UserForm_Activate()
    On Error Resume Next
    PWFitLayout
    PWRelayoutLists
End Sub

Private Sub PWHeadStyle(ByVal lbl As MSForms.Label, ByVal text As String)
    lbl.Caption = text
    lbl.Height = 14
    lbl.Font.Bold = True
    lbl.Font.Size = 8
End Sub

' ---------- 尺寸 ----------

Private Sub PWFitWindow()
    Dim maxH As Single, maxW As Single

    maxH = 540
    maxW = 880
    On Error Resume Next
    If Application.ActiveWindow.Height > 0 Then
        If Application.ActiveWindow.Height - 60 < maxH Then maxH = Application.ActiveWindow.Height - 60
    End If
    If Application.ActiveWindow.Width > 0 Then
        If Application.ActiveWindow.Width - 60 < maxW Then maxW = Application.ActiveWindow.Width - 60
    End If
    On Error GoTo 0
    If maxH < 380 Then maxH = 380
    If maxW < 720 Then maxW = 720
    Me.Height = maxH
    Me.Width = maxW
End Sub

Private Sub PWFitLayout()
    Dim w As Single, h As Single, listH As Single

    ' 注意：UserForm 在 Show 之前 InsideWidth/InsideHeight 不可靠，用设计尺寸减边框算
    w = Me.Width - 6
    h = Me.Height - 24
    If w < 600 Then w = 600

    labSearchTag.Move EDGE, 14, 34, 18
    btnAll.Move w - EDGE - 80, 12, 80, 22
    btnSearch.Move btnAll.Left - 8 - 64, 12, 64, 22
    txtQuery.Move EDGE + 38, 12, btnSearch.Left - 8 - (EDGE + 38), 22

    labCatTag.Move EDGE, 44, CAT_W, 14
    mPaneTop = 62

    mPaperX = EDGE + CAT_W + 12
    mPaperW = w - mPaperX - EDGE
    If mPaperW < 320 Then mPaperW = 320
    mTitleW = mPaperW - AUTH_W - YEAR_W - VEN_W - 17
    If mTitleW < 120 Then mTitleW = 120

    labHeadTitle.Move mPaperX, 44, mTitleW, 14
    labHeadAuthors.Move mPaperX + mTitleW, 44, AUTH_W, 14
    labHeadYear.Move mPaperX + mTitleW + AUTH_W, 44, YEAR_W, 14
    labHeadVenue.Move mPaperX + mTitleW + AUTH_W + YEAR_W, 44, VEN_W, 14

    btnClose.Move w - EDGE - 68, h - 34, 68, 26
    btnInsert.Move btnClose.Left - 8 - 100, h - 34, 100, 26
    labStatus.Move EDGE, h - 40, btnInsert.Left - EDGE - 8, 18
    labTip.Move EDGE, h - 58, btnInsert.Left - EDGE - 8, 16
    labDivider.Move EDGE, h - 64, w - 2 * EDGE, 1

    listH = (h - 68) - mPaneTop
    If listH < 80 Then listH = 80
    mPaneH = listH

    lstCategories.Move EDGE, mPaneTop, CAT_W, listH
    ' 先设列宽再 Move：MSForms 设 ColumnWidths 时会把控件宽度重置，顺序错了列表会被压扁
    On Error Resume Next
    lstPapers.ColumnWidths = CStr(mTitleW) & ";" & CStr(AUTH_W) & ";" & CStr(YEAR_W) & ";" & CStr(VEN_W)
    On Error GoTo 0
    lstPapers.Move mPaperX, mPaneTop, mPaperW, listH
End Sub

' Clear 会把 ListBox 尺寸重置回设计值，所以每次 Clear 之后要重新摆一次
Private Sub PWRelayoutLists()
    On Error Resume Next
    lstCategories.Left = EDGE
    lstCategories.Top = mPaneTop
    lstCategories.Width = CAT_W
    lstCategories.Height = mPaneH
    lstPapers.Left = mPaperX
    lstPapers.Top = mPaneTop
    lstPapers.Width = mPaperW
    lstPapers.Height = mPaneH
    On Error GoTo 0
End Sub

' ---------- 分类 ----------

Private Sub PWLoadCategories()
    Dim raw As String, lines() As String, parts() As String
    Dim i As Long, n As Long, papers As Long, uncat As Long
    Dim labels() As String, paths() As String, pos As Long, leaf As String
    Dim leafs() As String, depths() As Long, cnts() As Long

    mLoadingCats = True
    lstCategories.Clear
    PWRelayoutLists
    ReDim mScopes(0)
    mScopes(0) = ""
    ReDim labels(0)
    ReDim paths(0)
    papers = 0
    uncat = 0
    n = 0

    raw = PWGet("/api/word/categories.txt")
    If Left$(raw, 4) = "ERR:" Then
        lstCategories.AddItem "我的文库"
        mCatCount = 1
        mLoadingCats = False
        Exit Sub
    End If

    lines = Split(Replace(raw, vbCrLf, vbLf), vbLf)
    For i = 0 To UBound(lines)
        If Len(lines(i)) > 0 Then
            parts = Split(lines(i), vbTab)
            If Left$(lines(i), 1) = "#" Then
                If UBound(parts) >= 3 Then
                    papers = Val(parts(1))
                    uncat = Val(parts(2))
                End If
            ElseIf UBound(parts) >= 2 Then
                ReDim Preserve labels(n)
                ReDim Preserve paths(n)
                ReDim Preserve leafs(n)
                ReDim Preserve depths(n)
                ReDim Preserve cnts(n)
                leaf = parts(1)
                pos = InStrRev(parts(1), " / ")
                If pos > 0 Then leaf = Mid$(parts(1), pos + 3)
                leafs(n) = leaf
                depths(n) = Val(parts(0))
                cnts(n) = Val(parts(2))
                paths(n) = parts(1)
                n = n + 1
            End If
        End If
    Next i

    ' 用 ├─ / └─ / │ 画出层级，比单纯缩进清楚
    ReDim labels(n - 1)
    For i = 0 To n - 1
        labels(i) = PWTreePrefix(depths, n, i) & leafs(i) & "（" & cnts(i) & " 篇）"
    Next i

    ReDim mScopes(n + 1)
    mScopes(0) = ""
    mScopes(1) = "__none__"
    lstCategories.AddItem "我的文库（" & papers & " 篇）"
    lstCategories.AddItem "未分类（" & uncat & " 篇）"
    For i = 0 To n - 1
        mScopes(i + 2) = paths(i)
        lstCategories.AddItem labels(i)
    Next i

    mCatCount = lstCategories.ListCount
    lstCategories.ListIndex = 0
    mLoadingCats = False
End Sub

Private Function PWTreePrefix(depths() As Long, ByVal count As Long, ByVal i As Long) As String
    Dim level As Long, j As Long, pfx As String, hasSibling As Boolean, isLast As Boolean

    For level = 0 To depths(i) - 1
        hasSibling = False
        For j = i + 1 To count - 1
            If depths(j) <= level Then
                If depths(j) = level Then hasSibling = True
                Exit For
            End If
        Next j
        If hasSibling Then
            pfx = pfx & ChrW(&H2502) & "  "     ' │
        Else
            pfx = pfx & "   "
        End If
    Next level
    If depths(i) > 0 Then
        isLast = True
        For j = i + 1 To count - 1
            If depths(j) < depths(i) Then Exit For
            If depths(j) = depths(i) Then
                isLast = False
                Exit For
            End If
        Next j
        If isLast Then
            pfx = pfx & ChrW(&H2514) & ChrW(&H2500) & " "     ' └─
        Else
            pfx = pfx & ChrW(&H251C) & ChrW(&H2500) & " "     ' ├─
        End If
    End If
    PWTreePrefix = pfx
End Function

Private Function PWCurrentScope() As String
    Dim idx As Long

    idx = lstCategories.ListIndex
    If idx >= 0 And idx <= UBound(mScopes) Then PWCurrentScope = mScopes(idx)
End Function

' ---------- 文献 ----------

Private Sub PWLoadPapers(ByVal q As String, ByVal scope As String)
    Dim raw As String, lines() As String, parts() As String
    Dim i As Long, row As Long, total As Long
    Dim path As String

    lstPapers.Clear
    PWRelayoutLists
    ReDim mIds(0)
    labTip.Caption = "正在加载…"
    labStatus.Caption = ""
    txtQuery.Enabled = False
    btnSearch.Enabled = False
    btnAll.Enabled = False
    btnInsert.Enabled = False
    lstCategories.Enabled = False

    path = "/api/word/papers.txt?q=" & PWUrlEncode(Trim$(q))
    If Len(scope) > 0 Then path = path & "&scope=" & PWUrlEncode(scope)
    path = path & "&limit=500"
    raw = PWGet(path)

    txtQuery.Enabled = True
    btnSearch.Enabled = True
    btnAll.Enabled = True
    btnInsert.Enabled = True
    lstCategories.Enabled = True

    If Left$(raw, 4) = "ERR:" Then
        labStatus.Caption = "连不上 NPL：" & Mid$(raw, 6) & "（请先启动桌面程序）"
        Exit Sub
    End If

    row = -1
    lines = Split(Replace(raw, vbCrLf, vbLf), vbLf)
    For i = 0 To UBound(lines)
        If Len(lines(i)) > 0 Then
            parts = Split(lines(i), vbTab)
            If Left$(lines(i), 1) = "#" Then
                If UBound(parts) >= 2 Then total = Val(parts(1))
            ElseIf UBound(parts) >= 5 Then
                row = row + 1
                If row = 0 Then
                    ReDim mIds(0)
                Else
                    ReDim Preserve mIds(row)
                End If
                mIds(row) = parts(0)
                If parts(5) = "0" Then
                    lstPapers.AddItem "[无PDF] " & parts(3)
                Else
                    lstPapers.AddItem parts(3)
                End If
                lstPapers.List(row, 1) = parts(2)
                lstPapers.List(row, 2) = parts(1)
                lstPapers.List(row, 3) = parts(4)
            End If
        End If
    Next i

    If lstPapers.ListCount = 0 Then
        labStatus.Caption = "这个分类 / 关键词下没有文献。"
        labTip.Caption = "换个分类或清空搜索框。"
        Exit Sub
    End If

    If total > lstPapers.ListCount Then
        labStatus.Caption = "共 " & total & " 篇，当前显示前 " & lstPapers.ListCount & " 篇（输入关键词缩小范围）"
    Else
        labStatus.Caption = "共 " & lstPapers.ListCount & " 篇"
    End If
    labTip.Caption = "按住 Ctrl / Shift 多选；双击某行直接插入。"
    lstPapers.ListIndex = 0
    PWRelayoutLists
End Sub

Private Function PWSelectionIds() As String
    Dim i As Long, out As String, n As Long

    For i = 0 To lstPapers.ListCount - 1
        If lstPapers.Selected(i) Then
            If i <= UBound(mIds) Then
                If Len(out) > 0 Then out = out & ","
                out = out & mIds(i)
                n = n + 1
            End If
        End If
    Next i
    If n > 0 Then PWSelectionIds = out
End Function

Private Sub PWInsertSelected()
    Dim ids As String

    ids = PWSelectionIds()
    If Len(ids) = 0 Then
        labStatus.Caption = "还没有选中文献：点一行，或按住 Ctrl / Shift 多选。"
        Exit Sub
    End If
    gPickerIds = ids
    Unload Me
End Sub

' ---------- 事件 ----------

Private Sub btnSearch_Click()
    PWLoadPapers txtQuery.Value, PWCurrentScope()
End Sub

Private Sub txtQuery_KeyDown(ByVal KeyCode As MSForms.ReturnInteger, ByVal Shift As Integer)
    If KeyCode = 13 Then
        KeyCode = 0
        PWLoadPapers txtQuery.Value, PWCurrentScope()
    End If
End Sub

Private Sub lstCategories_Click()
    If mLoadingCats Then Exit Sub
    On Error Resume Next
    PWLoadPapers txtQuery.Value, PWCurrentScope()
End Sub

Private Sub btnAll_Click()
    Dim i As Long

    If lstPapers.ListCount = 0 Then Exit Sub
    For i = 0 To lstPapers.ListCount - 1
        lstPapers.Selected(i) = True
    Next i
    labStatus.Caption = "已全选 " & lstPapers.ListCount & " 篇"
End Sub

Private Sub btnInsert_Click()
    PWInsertSelected
End Sub

Private Sub btnClose_Click()
    gPickerIds = ""
    Unload Me
End Sub

Private Sub lstPapers_DblClick(ByVal Cancel As MSForms.ReturnBoolean)
    If lstPapers.ListIndex >= 0 Then lstPapers.Selected(lstPapers.ListIndex) = True
    PWInsertSelected
End Sub

Private Sub lstPapers_KeyDown(ByVal KeyCode As MSForms.ReturnInteger, ByVal Shift As Integer)
    If KeyCode = 13 Then
        KeyCode = 0
        PWInsertSelected
    End If
End Sub

' ---------- 主题 ----------

Private Sub PWApplyTheme()
    On Error Resume Next
    Me.BackColor = RGB(244, 246, 249)

    txtQuery.BackColor = vbWhite
    txtQuery.ForeColor = RGB(16, 32, 58)
    txtQuery.BorderStyle = 1
    txtQuery.BorderColor = RGB(203, 213, 225)

    lstCategories.BackColor = vbWhite
    lstCategories.ForeColor = RGB(16, 32, 58)
    lstCategories.SpecialEffect = 0

    lstPapers.BackColor = vbWhite
    lstPapers.ForeColor = RGB(16, 32, 58)
    lstPapers.SpecialEffect = 0

    btnSearch.BackColor = vbWhite
    btnSearch.ForeColor = RGB(16, 32, 58)
    btnAll.BackColor = vbWhite
    btnAll.ForeColor = RGB(16, 32, 58)
    btnClose.BackColor = vbWhite
    btnClose.ForeColor = RGB(16, 32, 58)
    btnInsert.BackColor = RGB(14, 124, 134)
    btnInsert.ForeColor = vbWhite
    btnInsert.Font.Bold = True

    labSearchTag.ForeColor = RGB(107, 124, 143)
    labCatTag.ForeColor = RGB(16, 32, 58)
    labHeadYear.ForeColor = RGB(16, 32, 58)
    labHeadAuthors.ForeColor = RGB(16, 32, 58)
    labHeadTitle.ForeColor = RGB(16, 32, 58)
    labHeadVenue.ForeColor = RGB(16, 32, 58)
    labTip.ForeColor = RGB(107, 124, 143)
    labTip.Font.Size = 8
    labStatus.ForeColor = RGB(107, 124, 143)
    labStatus.Font.Size = 8
End Sub

' ---------- 自动化入口（E2E 用） ----------

' 按 1 开始的分类行号选中分类并重新加载，返回列表行数
Public Function PWTestSelectCategory(ByVal rowIndex As Long) As Long
    Dim idx As Long

    idx = rowIndex - 1
    If idx < 0 Then idx = 0
    If idx >= lstCategories.ListCount Then idx = lstCategories.ListCount - 1
    If idx < 0 Then
        PWTestSelectCategory = 0
        Exit Function
    End If
    mLoadingCats = False
    lstCategories.ListIndex = idx
    PWLoadPapers txtQuery.Value, PWCurrentScope()
    PWTestSelectCategory = lstPapers.ListCount
End Function

' 按 gPickerRows（"1,2,3"，1 开始的行号）选中并返回 ids
Public Function PWTestPickRows() As String
    Dim parts() As String, i As Long, idx As Long

    If Len(gPickerRows) = 0 Then gPickerRows = "1"
    parts = Split(gPickerRows, ",")
    For i = 0 To UBound(parts)
        idx = Val(Trim$(parts(i))) - 1
        If idx >= 0 And idx < lstPapers.ListCount Then lstPapers.Selected(idx) = True
    Next i
    PWTestPickRows = PWSelectionIds()
End Function
