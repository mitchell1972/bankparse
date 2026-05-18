/*
 * BankScan AI — HMRC fraud-prevention browser collector.
 *
 * Collects the 5 browser-side fields HMRC mandates for the WEB_APP_VIA_SERVER
 * connection method, then POSTs them to /api/hmrc/fraud-context. The server
 * pairs these with its own observed values (Public-IP, Public-Port, etc.)
 * on every outbound MTD call.
 *
 * Reference: memory/reference_hmrc_fraud_prevention_headers.md
 *
 * Loaded on /hmrc/connect, but also fire-on-import safe everywhere — checks
 * for the bp_csrf cookie before posting so it no-ops on logged-out pages.
 */
(function () {
    "use strict";

    var DEVICE_ID_KEY = "bp_hmrc_device_id";

    function getDeviceId() {
        // CRITICAL: persistent across sessions, never rotates.
        // Stored in localStorage; if cleared, recreated. HMRC accepts this
        // because it's still durable for the typical user.
        var id = localStorage.getItem(DEVICE_ID_KEY);
        if (!id) {
            // RFC 4122 v4 UUID using crypto.getRandomValues — same shape HMRC's
            // examples use.
            var bytes = new Uint8Array(16);
            (window.crypto || window.msCrypto).getRandomValues(bytes);
            // version + variant bits
            bytes[6] = (bytes[6] & 0x0f) | 0x40;
            bytes[8] = (bytes[8] & 0x3f) | 0x80;
            var hex = Array.from(bytes).map(function (b) {
                return b.toString(16).padStart(2, "0");
            }).join("");
            id = hex.slice(0, 8) + "-" + hex.slice(8, 12) + "-" +
                 hex.slice(12, 16) + "-" + hex.slice(16, 20) + "-" +
                 hex.slice(20, 32);
            localStorage.setItem(DEVICE_ID_KEY, id);
        }
        return id;
    }

    function timezoneOffsetUTC() {
        // HMRC requires the form UTC±hh:mm (e.g. UTC+01:00 or UTC-05:30).
        var mins = -new Date().getTimezoneOffset();
        var sign = mins >= 0 ? "+" : "-";
        var abs = Math.abs(mins);
        var hh = String(Math.floor(abs / 60)).padStart(2, "0");
        var mm = String(abs % 60).padStart(2, "0");
        return "UTC" + sign + hh + ":" + mm;
    }

    function screensList() {
        // HMRC accepts a list; we currently report the single primary screen.
        // window.screen.colorDepth is what colour-depth maps to.
        var s = window.screen || {};
        return [{
            width: s.width || 0,
            height: s.height || 0,
            "scaling-factor": window.devicePixelRatio || 1,
            "colour-depth": s.colorDepth || 24
        }];
    }

    function windowSize() {
        return { width: window.innerWidth || 0, height: window.innerHeight || 0 };
    }

    function csrfFromCookie() {
        var m = document.cookie.match(/(?:^|;\s*)bp_csrf=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : "";
    }

    function authed() {
        // Skip if user isn't logged in (no bp_auth cookie).
        return /(^|;\s*)bp_auth=/.test(document.cookie);
    }

    function send() {
        if (!authed()) return;

        var payload = {
            device_id: getDeviceId(),
            browser_user_agent: navigator.userAgent || "",
            timezone: timezoneOffsetUTC(),
            screens: screensList(),
            window: windowSize(),
            mfa: []  // populated later if/when we add MFA to BankScan login
        };

        var csrf = csrfFromCookie();
        var headers = { "Content-Type": "application/json" };
        if (csrf) headers["X-CSRF-Token"] = csrf;

        fetch("/api/hmrc/fraud-context", {
            method: "POST",
            credentials: "same-origin",
            headers: headers,
            body: JSON.stringify(payload)
        }).catch(function () {
            // Best-effort. A failure here doesn't block the user from doing
            // anything else; the next outbound HMRC call will simply use
            // stale/no browser-side data and fail the validator, which is
            // the right loud signal.
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", send);
    } else {
        send();
    }
})();
