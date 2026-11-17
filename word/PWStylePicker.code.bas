Option Explicit

' ============================================================
' 「NPL 引文格式」窗口（UserForm 名：PWStylePicker）
'
' 从 GET /api/cite/styles.txt 拉格式列表（id <TAB> name <TAB> 数值型?），
' 用可搜索的列表展示（格式多了也不用像 InputBox 那样编号）。
' 选中后写回 gStylePicked / gStylePickedName，由 PWSetStyle 落盘并重排。
' ============================================================

Private Const EDGE As Single = 12

Private WithEvents txtSearch As MSForms.TextBox
Private WithEvents lstStyles As MSForms.ListBox
Private WithEvents btnOK As MSForms.CommandButton
Private WithEvents btnCancel As MSForms.CommandButton
Private labSearchTag As MSForms.Label
Private labHint As MSForms.Label

Private mIds() As String
Private mNames() As String
Private mNumeric() As Boolean
Private mCount As Long

Private Sub UserForm_Initialize()
    On Error GoTo EH
    Me.Caption = "NPL 引文格式"
    Me.Width = 460
    Me.Height = 420
    On Error Resume Next
    Me.Font.Name = "Microsoft YaHei"
    Me.Font.Size = 10
    On Error GoTo 0

    Set labSearchTag = Me.Controls.Add("Forms.Label.1", "labSearchTag", True)
    labSearchTag.Caption = "搜索"
    labSearchTag.Height = 18
    labSearchTag.Width = 34

    Set txtSearch = Me.Controls.Add("Forms.TextBox.1", "txtSearch", True)
    txtSearch.Height = 22

    Set lstStyles = Me.Controls.Add("Forms.ListBox.1", "lstStyles", True)
    lstStyles.IntegralHeight = False
    lstStyles.Font.Size = 10

    Set labHint = Me.Controls.Add("Forms.Label.1", "labHint", True)
    labHint.Caption = "自定义格式在 NPL「设置 → 引文格式」里用一句话生成或导入 .csl。"
    labHint.Font.Size = 8

    Set btnOK = Me.Controls.Add("Forms.CommandButton.1", "btnOK", True)
    With btnOK
        .Caption = "使用这个格式"
        .Default = True
        .Height = 26
        .Width = 110
    End With
    Set btnCancel = Me.Controls.Add("Forms.CommandButton.1", "btnCancel", True)
    With btnCancel
        .Caption = "取消"
        .Cancel = True
        .Height = 26
        .Width = 68
    End With

    PWFitLayout
    PWApplyTheme
    PWLoadStyles
    PWFilterStyles ""
    Exit Sub
EH:
    On Error Resume Next
    If Not labHint Is Nothing Then labHint.Caption = "窗口初始化失败：" & Err.Description
End Sub

Private Sub PWFitLayout()
    Dim w As Single, h As Single

    w = Me.Width - 6
    h = Me.Height - 24
    labSearchTag.Move EDGE, 14, 34, 18
    txtSearch.Move EDGE + 38, 12, w - (EDGE + 38) - EDGE, 22
    lstStyles.Move EDGE, 42, w - 2 * EDGE, h - 42 - 74
    labHint.Move EDGE, h - 66, w - 2 * EDGE, 16
    btnCancel.Move w - EDGE - 68, h - 34, 68, 26
    btnOK.Move btnCancel.Left - 8 - 110, h - 34, 110, 26
End Sub

Private Sub PWApplyTheme()
    On Error Resume Next
    Me.BackColor = RGB(244, 246, 249)
    txtSearch.BackColor = vbWhite
    txtSearch.ForeColor = RGB(16, 32, 58)
    txtSearch.BorderStyle = 1
    txtSearch.BorderColor = RGB(203, 213, 225)
    lstStyles.BackColor = vbWhite
    lstStyles.ForeColor = RGB(16, 32, 58)
    lstStyles.SpecialEffect = 0
    labSearchTag.ForeColor = RGB(107, 124, 143)
    labHint.ForeColor = RGB(107, 124, 143)
    btnOK.BackColor = RGB(14, 124, 134)
    btnOK.ForeColor = vbWhite
    btnOK.Font.Bold = True
    btnCancel.BackColor = vbWhite
    btnCancel.ForeColor = RGB(16, 32, 58)
End Sub

Private Sub PWLoadStyles()
    Dim raw As String, lines() As String, parts() As String, i As Long

    mCount = 0
    ReDim mIds(0)
    ReDim mNames(0)
    ReDim mNumeric(0)

    raw = PWGet("/api/cite/styles.txt")
    If Left$(raw, 4) = "ERR:" Then
        labHint.Caption = "读取格式列表失败：" & Mid$(raw, 6)
        Exit Sub
    End If

    lines = Split(Replace(raw, vbCrLf, vbLf), vbLf)
    For i = 0 To UBound(lines)
        If Len(lines(i)) > 0 Then
            parts = Split(lines(i), vbTab)
            If UBound(parts) >= 1 Then
                ReDim Preserve mIds(mCount)
                ReDim Preserve mNames(mCount)
                ReDim Preserve mNumeric(mCount)
                mIds(mCount) = parts(0)
                mNames(mCount) = parts(1)
                If UBound(parts) >= 2 Then mNumeric(mCount) = (Val(parts(2)) = 1)
                mCount = mCount + 1
            End If
        End If
    Next i
End Sub

Private Sub PWFilterStyles(ByVal q As String)
    Dim i As Long, label As String

    lstStyles.Clear
    q = LCase$(Trim$(q))
    For i = 0 To mCount - 1
        label = mNames(i)
        If mNumeric(i) Then label = label & "（数字型）"
        If mIds(i) = gStyleCurrent Then label = label & "   ← 当前"
        If Len(q) = 0 Or InStr(LCase$(mNames(i)), q) > 0 Or InStr(LCase$(mIds(i)), q) > 0 Then
            lstStyles.AddItem label
            If lstStyles.ListCount = 1 And mIds(i) = gStyleCurrent Then
                lstStyles.ListIndex = 0
            End If
        End If
    Next i
    ' 列表用空格分隔 id 和名字，选中时按 ListIndex 找不到原 id，所以这里改用并行数组
    If lstStyles.ListCount = 0 Then labHint.Caption = "没有匹配的格式。"
End Sub

Private Function PWSelectedStyleIndex() As Long
    Dim i As Long, shown As Long, k As Long
    Dim q As String

    q = LCase$(Trim$(txtSearch.Value))
    shown = -1
    For i = 0 To mCount - 1
        If Len(q) = 0 Or InStr(LCase$(mNames(i)), q) > 0 Or InStr(LCase$(mIds(i)), q) > 0 Then
            shown = shown + 1
            If shown = lstStyles.ListIndex Then
                PWSelectedStyleIndex = i
                Exit Function
            End If
        End If
    Next i
    PWSelectedStyleIndex = -1
End Function

Private Sub PWAccept()
    Dim idx As Long

    idx = PWSelectedStyleIndex()
    If idx < 0 Then
        labHint.Caption = "先选一个格式。"
        Exit Sub
    End If
    gStylePicked = mIds(idx)
    gStylePickedName = mNames(idx)
    Unload Me
End Sub

Private Sub txtSearch_Change()
    PWFilterStyles txtSearch.Value
End Sub

Private Sub txtSearch_KeyDown(ByVal KeyCode As MSForms.ReturnInteger, ByVal Shift As Integer)
    If KeyCode = 13 Then
        KeyCode = 0
        If lstStyles.ListCount > 0 Then
            If lstStyles.ListIndex < 0 Then lstStyles.ListIndex = 0
            PWAccept
        End If
    End If
End Sub

Private Sub lstStyles_DblClick(ByVal Cancel As MSForms.ReturnBoolean)
    PWAccept
End Sub

Private Sub btnOK_Click()
    PWAccept
End Sub

Private Sub btnCancel_Click()
    gStylePicked = ""
    Unload Me
End Sub
