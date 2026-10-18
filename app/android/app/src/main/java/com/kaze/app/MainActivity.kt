// The whole shell: a full-screen WebView that opens the Kaze server, answers its Basic-auth challenge with the token
// from local.properties, pulls to refresh and walks its own history on back. What is on screen lives on the Mac.
package com.kaze.app

import android.annotation.SuppressLint
import android.net.Uri
import android.os.Bundle
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.HttpAuthHandler
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout

class MainActivity : AppCompatActivity() {

    private lateinit var web: WebView
    private lateinit var refresh: SwipeRefreshLayout
    private val serverHost: String? = Uri.parse(BuildConfig.KAZE_URL).host

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.statusBarColor = STATUS_BAR
        window.navigationBarColor = SURFACE

        web = WebView(this).apply {
            setBackgroundColor(PAGE)
            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                cacheMode = WebSettings.LOAD_DEFAULT
                allowFileAccess = false
                allowContentAccess = false
                setSupportZoom(false)
                builtInZoomControls = false
                displayZoomControls = false
            }
            webViewClient = ServerClient()
            isVerticalScrollBarEnabled = false
            overScrollMode = WebView.OVER_SCROLL_NEVER
        }

        refresh = SwipeRefreshLayout(this).apply {
            setColorSchemeColors(BLUE)
            setProgressBackgroundColorSchemeColor(SURFACE)
            addView(web, ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
            setOnRefreshListener { reloadFromServer() }
        }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.canGoBack()) {
                    web.goBack()
                } else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })

        setContentView(refresh)
        if (savedInstanceState == null) web.loadUrl(BuildConfig.KAZE_URL + "/")
    }

    /** Pull-to-refresh: reload the page, or go back to the server when the offline page is what is showing
     *  (reloading that page would only redraw it). */
    private fun reloadFromServer() {
        val host = web.url?.let { Uri.parse(it).host }
        if (host != null && host.equals(serverHost, ignoreCase = true)) web.reload()
        else web.loadUrl(BuildConfig.KAZE_URL + "/")
    }

    override fun onResume() {
        super.onResume()
        web.onResume()
    }

    override fun onPause() {
        web.onPause()
        super.onPause()
    }

    override fun onDestroy() {
        refresh.removeAllViews()
        web.destroy()
        super.onDestroy()
    }

    /** Answers the server's 401 for every request (the page, its files, its fetches) and keeps the shell on
     *  that one host, so the token never travels anywhere else. */
    private inner class ServerClient : WebViewClient() {

        override fun onReceivedHttpAuthRequest(
            view: WebView?, handler: HttpAuthHandler?, host: String?, realm: String?
        ) {
            if (handler == null) return
            if (host != null && host.equals(serverHost, ignoreCase = true)) {
                handler.proceed(AUTH_USER, BuildConfig.KAZE_TOKEN)
            } else {
                handler.cancel()
            }
        }

        override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
            val host = request?.url?.host ?: return true
            return !host.equals(serverHost, ignoreCase = true)
        }

        override fun onReceivedError(view: WebView?, request: WebResourceRequest?, error: WebResourceError?) {
            if (request?.isForMainFrame != true) return
            view?.loadDataWithBaseURL(null, OFFLINE_HTML, "text/html", "utf-8", null)
        }

        override fun onPageFinished(view: WebView?, url: String?) {
            refresh.isRefreshing = false
        }
    }

    private companion object {
        const val AUTH_USER = "kaze"
        const val STATUS_BAR = 0xFF1F2622.toInt()
        const val PAGE = 0xFFEDE9E2.toInt()
        const val SURFACE = 0xFFF4F1EB.toInt()
        const val BLUE = 0xFF2F44B8.toInt()

        val OFFLINE_HTML = """
            <html><head><meta name="viewport" content="width=device-width,initial-scale=1">
            <style>
              html,body{margin:0;height:100%;background:#EDE9E2;color:#535B52;
                font:15px/1.5 system-ui,Roboto,sans-serif;
                display:flex;align-items:center;justify-content:center;text-align:center}
              div{padding:0 28px;max-width:22em}
              b{color:#161A17;font-weight:700;display:block;margin-bottom:8px}
              span{color:#5C6459;font-size:13px;display:block;margin-top:14px}
            </style></head><body><div>
              <b>Can't reach Kaze</b>
              Either this phone has no connection, or the Mac is not answering.
              <span>Pull down to try again.</span>
            </div></body></html>
        """.trimIndent()
    }
}
