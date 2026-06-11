' Launches run_monitor.cmd without flashing a console window.
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
Set shell = CreateObject("WScript.Shell")
shell.Run """" & scriptDir & "\run_monitor.cmd""", 0, False
