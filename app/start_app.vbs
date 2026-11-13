' Starts Comfy Studio without a console window (from wherever this folder is).
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
If fso.FileExists(here & "\data\STOP") Then fso.DeleteFile here & "\data\STOP"
sh.CurrentDirectory = here
py = ""
' the setup writes this file with "echo", which ends it with a line break; VBScript's Trim only removes spaces,
' so the line break would become part of the path and nothing would start (fixed 2026-11-13)
If fso.FileExists(here & "\data\python_path.txt") Then py = Trim(Replace(Replace(fso.OpenTextFile(here & "\data\python_path.txt").ReadAll, vbCr, ""), vbLf, ""))
If py = "" Then py = "pythonw"
sh.Run """" & py & """ """ & here & "\ComfyStudio.pyw""", 0, False
