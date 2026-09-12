package com.zhirun.fertigation;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.JsResult;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final String APP_URL = "http://8.145.49.45/";
    private static final String VERSION_URL = "http://8.145.49.45/app/version";
    private static final String PREFERENCES = "zhirun_app";
    private static final String CONTENT_VERSION = "content_version";

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private WebView webView;
    private ProgressBar progressBar;
    private View offlineView;
    private boolean initialPageRequested;
    private boolean updateDialogVisible;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(Color.rgb(18, 76, 43));
        getWindow().setNavigationBarColor(Color.rgb(238, 243, 239));
        createContentView();
        configureWebView();

        if (savedInstanceState != null && webView.restoreState(savedInstanceState) != null) {
            initialPageRequested = true;
            checkForContentUpdate(false);
        } else {
            checkForContentUpdate(true);
        }
    }

    private void createContentView() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(238, 243, 239));
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            root.setOnApplyWindowInsetsListener((view, insets) -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                    view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
                } else {
                    view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                            insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
                }
                return insets;
            });
        }

        webView = new WebView(this);
        root.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setMax(100);
        FrameLayout.LayoutParams progressParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(3), Gravity.TOP);
        root.addView(progressBar, progressParams);

        offlineView = buildOfflineView();
        offlineView.setVisibility(View.GONE);
        root.addView(offlineView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);
    }

    private View buildOfflineView() {
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setGravity(Gravity.CENTER);
        panel.setPadding(dp(30), dp(30), dp(30), dp(30));
        panel.setBackgroundColor(Color.rgb(238, 243, 239));

        TextView title = new TextView(this);
        title.setText("暂时无法连接智润");
        title.setTextSize(22);
        title.setTextColor(Color.rgb(29, 41, 33));
        title.setGravity(Gravity.CENTER);
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        panel.addView(title);

        TextView detail = new TextView(this);
        detail.setText("请检查手机网络后重试。账号和设备绑定信息不会丢失。");
        detail.setTextSize(14);
        detail.setTextColor(Color.rgb(104, 117, 108));
        detail.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams detailParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        detailParams.setMargins(0, dp(12), 0, dp(24));
        panel.addView(detail, detailParams);

        Button retry = new Button(this);
        retry.setText("重新连接");
        retry.setAllCaps(false);
        retry.setTextColor(Color.WHITE);
        retry.setBackgroundColor(Color.rgb(22, 133, 62));
        retry.setOnClickListener(view -> {
            offlineView.setVisibility(View.GONE);
            loadApplication(null);
        });
        panel.addView(retry, new LinearLayout.LayoutParams(dp(170), dp(48)));
        return panel;
    }

    @SuppressWarnings("SetJavaScriptEnabled")
    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setUserAgentString(settings.getUserAgentString() + " ZhiRunAndroid/1.0");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WebView.startSafeBrowsing(this, null);
        }

        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(webView, true);

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int progress) {
                progressBar.setProgress(progress);
                progressBar.setVisibility(progress >= 100 ? View.GONE : View.VISIBLE);
            }

            @Override
            public boolean onJsAlert(WebView view, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                        .setMessage(message)
                        .setPositiveButton("确定", (dialog, which) -> result.confirm())
                        .setOnCancelListener(dialog -> result.cancel())
                        .show();
                return true;
            }
        });

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String scheme = uri.getScheme();
                if ("http".equalsIgnoreCase(scheme) || "https".equalsIgnoreCase(scheme)) {
                    return false;
                }
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, uri));
                } catch (Exception ignored) {
                    Toast.makeText(MainActivity.this, "无法打开该链接", Toast.LENGTH_SHORT).show();
                }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                offlineView.setVisibility(View.GONE);
                CookieManager.getInstance().flush();
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) offlineView.setVisibility(View.VISIBLE);
            }

            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, android.net.http.SslError error) {
                handler.cancel();
                Toast.makeText(MainActivity.this, "服务器证书校验失败", Toast.LENGTH_LONG).show();
            }
        });

        webView.setDownloadListener(createDownloadListener());
    }

    private DownloadListener createDownloadListener() {
        return (url, userAgent, contentDisposition, mimeType, contentLength) -> {
            try {
                DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
                request.setMimeType(mimeType);
                request.addRequestHeader("User-Agent", userAgent);
                String cookie = CookieManager.getInstance().getCookie(url);
                if (cookie != null) request.addRequestHeader("Cookie", cookie);
                request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                request.setDestinationInExternalFilesDir(this, Environment.DIRECTORY_DOWNLOADS, "zhirun-export.xlsx");
                ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(request);
                Toast.makeText(this, "已开始下载", Toast.LENGTH_SHORT).show();
            } catch (Exception error) {
                Toast.makeText(this, "下载失败", Toast.LENGTH_SHORT).show();
            }
        };
    }

    private void checkForContentUpdate(boolean initial) {
        executor.execute(() -> {
            String remoteVersion = fetchContentVersion();
            runOnUiThread(() -> handleContentVersion(remoteVersion, initial));
        });
    }

    private String fetchContentVersion() {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(VERSION_URL).openConnection();
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(5000);
            connection.setUseCaches(false);
            if (connection.getResponseCode() != HttpURLConnection.HTTP_OK) return null;
            StringBuilder body = new StringBuilder();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                    connection.getInputStream(), StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) body.append(line);
            }
            return new JSONObject(body.toString()).optString("content_version", null);
        } catch (Exception ignored) {
            return null;
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private void handleContentVersion(String remoteVersion, boolean initial) {
        SharedPreferences preferences = getSharedPreferences(PREFERENCES, MODE_PRIVATE);
        String installedVersion = preferences.getString(CONTENT_VERSION, "");
        if (remoteVersion == null || remoteVersion.isEmpty()) {
            if (initial && !initialPageRequested) loadApplication(null);
            return;
        }
        if (installedVersion.isEmpty()) {
            preferences.edit().putString(CONTENT_VERSION, remoteVersion).apply();
            if (initial && !initialPageRequested) loadApplication(remoteVersion);
            return;
        }
        if (!installedVersion.equals(remoteVersion) && !updateDialogVisible) {
            showContentUpdate(remoteVersion);
        } else if (initial && !initialPageRequested) {
            loadApplication(remoteVersion);
        }
    }

    private void showContentUpdate(String remoteVersion) {
        updateDialogVisible = true;
        new AlertDialog.Builder(this)
                .setTitle("发现界面更新")
                .setMessage("智润已发布新的页面和功能，点击立即更新即可同步，无需重新安装 APK。")
                .setPositiveButton("立即更新", (dialog, which) -> {
                    getSharedPreferences(PREFERENCES, MODE_PRIVATE).edit()
                            .putString(CONTENT_VERSION, remoteVersion).apply();
                    webView.clearCache(false);
                    loadApplication(remoteVersion);
                    updateDialogVisible = false;
                })
                .setNegativeButton("稍后", (dialog, which) -> {
                    if (!initialPageRequested) loadApplication(null);
                    updateDialogVisible = false;
                })
                .setOnCancelListener(dialog -> {
                    if (!initialPageRequested) loadApplication(null);
                    updateDialogVisible = false;
                })
                .show();
    }

    private void loadApplication(String contentVersion) {
        initialPageRequested = true;
        offlineView.setVisibility(View.GONE);
        String url = contentVersion == null ? APP_URL : APP_URL + "?app_version=" + Uri.encode(contentVersion);
        webView.loadUrl(url);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (initialPageRequested) checkForContentUpdate(false);
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        webView.saveState(outState);
        super.onSaveInstanceState(outState);
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        executor.shutdownNow();
        if (webView != null) {
            webView.stopLoading();
            webView.destroy();
        }
        super.onDestroy();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
