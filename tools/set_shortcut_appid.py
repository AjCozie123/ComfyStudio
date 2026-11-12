"""Give a Windows shortcut (.lnk) Comfy Studio's app identity, so the taskbar shows the Comfy Studio logo for the
running window instead of Python's (Windows matches the window to the shortcut by this identity).

    python tools/set_shortcut_appid.py "C:\\Users\\me\\Desktop\\Comfy Studio.lnk" [app id]
"""
import sys


def stamp(lnk, app_id="ComfyStudio.Desktop"):
    import pythoncom
    from win32com.propsys import propsys, pscon
    from win32com.shell import shellcon
    store = propsys.SHGetPropertyStoreFromParsingName(lnk, None, shellcon.GPS_READWRITE, propsys.IID_IPropertyStore)
    store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(app_id, pythoncom.VT_LPWSTR))
    store.Commit()
    return str(store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    print("set:", stamp(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "ComfyStudio.Desktop"))
