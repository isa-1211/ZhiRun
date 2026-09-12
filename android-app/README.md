# ZhiRun Android

This project builds the mobile shell for the ZhiRun dashboard. The app renders
`http://8.145.49.45/` inside its own WebView; it does not open the system browser
for normal HTTP/HTTPS navigation and it does not bundle credentials.

## Content updates

On launch and resume, the app calls `/app/version`. The server derives
`content_version` from the deployed `server/index.html` and
`server/zhirun_server.py` SHA-256 digest. When that digest changes, the app
displays an in-app update dialog. Choosing **立即更新** clears the WebView
page cache and reloads the current server UI. Normal web changes therefore
require server deployment only, not a new APK installation.

Native changes such as Android permissions, the app icon, or Java code still
require a newly signed APK. Android requires the user to approve installation
of such an APK unless the device is managed or rooted.

## Build

Use JDK 17, Android SDK Platform 35, and Build Tools 35.0.0.

```powershell
cd android-app
.\build-apk.ps1
```

The build script uses the official Android command-line tools directly and
automatically maps this Chinese workspace to a temporary ASCII-only drive on
Windows. The generated file is `android-app/dist/ZhiRun-1.0.0.apk`.

The release signing key and `signing.properties` are intentionally ignored by
Git. Keep the key: all future APK updates must use the same signing identity.

The current server only supports HTTP, so the manifest temporarily permits
cleartext traffic. Replace `APP_URL` and `VERSION_URL` with an HTTPS domain and
disable cleartext traffic after the domain, certificate, and port 443 are ready.
