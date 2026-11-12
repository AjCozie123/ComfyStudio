' Starts Comfy Studio without a console window (from wherever this folder is).
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
If fso.FileExists(here & "\data\STOP") Then fso.DeleteFile here & "\data\STOP"
sh.CurrentDirectory = here
py = ""
If fso.FileExists(here & "\data\python_path.txt") Then py = Trim(fso.OpenTextFile(here & "\data\python_path.txt").ReadAll)
If py = "" Then py = "pythonw"
sh.Run """" & py & """ """ & here & "\ComfyStudio.pyw""", 0, False
