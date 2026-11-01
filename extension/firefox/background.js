
const API_URL = "https://clonepage.dledwith.com/api/reading";

async function showStatus(tabId, text, color, message) {
    await browser.action.setBadgeBackgroundColor({
        tabId,
        color
    });

    await browser.action.setBadgeText({
        tabId,
        text
    });

    await browser.action.setTitle({
        tabId,
        title: message
    });

    setTimeout(() => {
        browser.action.setBadgeText({
            tabId,
            text: ""
        }).catch(() => {});
    }, 3000);
}

browser.action.onClicked.addListener(async (tab) => {
    if (tab.id == null) return;

    const tabId = tab.id;
    const url = tab.url;

    if (!url || !/^https?:\/\//i.test(url)) {
        await showStatus(
            tabId, "N/A", "#777777",
            "This page cannot be saved"
        );
        return;
    }

    try {
        await showStatus(
            tabId, "...", "#64748b",
            "Saving to Clonepage..."
        );

        const response = await fetch(API_URL, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                url,
                title: (tab.title || "").slice(0, 200)
            }),
            signal: AbortSignal.timeout(10000)
        });

        if (response.status === 409) {
            await showStatus(
                tabId, "OLD", "#d49b50",
                "Already in your reading list"
            );
            return;
        }

        if (!response.ok) {
            throw new Error(`API returned ${response.status}`);
        }

        await showStatus(
            tabId, "OK", "#4a9b76",
            "Saved to Clonepage"
        );

    } catch (error) {
        console.error("Clonepage save failed:", error);

        await showStatus(
            tabId, "ERR", "#c75959",
            "Could not save. Check Tailscale and the API."
        );
    }
});
