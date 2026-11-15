' ==========================================================================
'  IP-виджет — запуск одним двойным щелчком, без окна консоли.
'  Файл только запускает ip_widget.pyw из этой же папки.
' ==========================================================================
Option Explicit

Dim fso, sh, baseDir, scriptPath, candidates(2), i, exe

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = fso.BuildPath(baseDir, "ip_widget.pyw")

If Not fso.FileExists(scriptPath) Then
    MsgBox "Рядом с этим файлом нет ip_widget.pyw." & vbCrLf & _
           "Скопируйте папку целиком.", vbCritical, "IP-виджет"
    WScript.Quit 1
End If

' Кандидаты на pythonw.exe: реестр (App Paths), затем PATH.
candidates(0) = TrimQuotes(RegReadSafe("HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\pythonw.exe\"))
candidates(1) = TrimQuotes(RegReadSafe("HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\pythonw.exe\"))
candidates(2) = "pythonw.exe"

For i = 0 To UBound(candidates)
    exe = candidates(i)
    If Len(exe) > 0 Then
        If exe = "pythonw.exe" Or fso.FileExists(exe) Then
            If TryRun(exe) Then WScript.Quit 0
        End If
    End If
Next

MsgBox "Не удалось запустить виджет: не найден pythonw.exe." & vbCrLf & vbCrLf & _
       "Установите Python 3 для Windows (python.org) с параметром «Add Python to PATH» " & _
       "или запустите вручную: python ip_widget.pyw", vbExclamation, "IP-виджет"
WScript.Quit 1

' --------------------------------------------------------------------------
Function TryRun(exe)
    Dim cmd
    cmd = """" & exe & """ """ & scriptPath & """"
    On Error Resume Next
    sh.Run cmd, 0, False
    TryRun = (Err.Number = 0)
    Err.Clear
    On Error GoTo 0
End Function

Function RegReadSafe(key)
    On Error Resume Next
    RegReadSafe = sh.RegRead(key)
    Err.Clear
    On Error GoTo 0
End Function

Function TrimQuotes(value)
    Dim s
    s = value
    If Len(s) >= 2 Then
        If Left(s, 1) = """" Then s = Mid(s, 2)
        If Right(s, 1) = """" Then s = Left(s, Len(s) - 1)
    End If
    TrimQuotes = Trim(s)
End Function
