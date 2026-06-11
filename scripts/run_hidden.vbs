' Runs any sibling .cmd without flashing a console window.
' Usage: wscript.exe run_hidden.vbs run_monitor.cmd
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
Set shell = CreateObject("WScript.Shell")
shell.Run """" & scriptDir & "\" & WScript.Arguments(0) & """", 0, False
