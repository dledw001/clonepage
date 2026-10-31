
<script lang="ts">
    import { onMount } from 'svelte';
    import {
        BookOpen, Plus, RefreshCw, Trash2,
        Circle, CircleCheck, ArrowUpRight
    } from '@lucide/svelte';

    type Article = {
        id: string;
        title: string;
        url: string;
        is_read: boolean;
        saved_at: string;
    };

    let articles = $state<Article[]>([]);
    let initialized = $state(false);
    let adding = $state(false);
    let saving = $state(false);
    let showAll = $state(false);
    let url = $state('');
    let title = $state('');
    let error = $state('');

    const unread = $derived(
        articles.filter(article => !article.is_read).length
    );

    const visible = $derived(
        showAll ? articles : articles.slice(0, 3)
    );

    async function refresh() {
        try {
            const response = await fetch('/api/reading');
            if (!response.ok) throw new Error('Cannot load reading list');

            articles = await response.json();
            error = '';
        } catch {
            error = 'Cannot reach the reading-list server.';
        } finally {
            initialized = true;
        }
    }

    onMount(() => {
        void refresh();

        const onVisibility = () => {
            if (!document.hidden) void refresh();
        };

        document.addEventListener('visibilitychange', onVisibility);

        return () => {
            document.removeEventListener('visibilitychange', onVisibility);
        };
    });

    async function save(event: SubmitEvent) {
        event.preventDefault();
        if (saving) return;

        let normalized: string;

        try {
            const input = url.trim();
            normalized = /^https?:\/\//i.test(input)
                ? input
                : `https://${input}`;

            const parsed = new URL(normalized);
            if (!['http:', 'https:'].includes(parsed.protocol)) {
                throw new Error('Invalid protocol');
            }
        } catch {
            error = 'Enter a valid website URL.';
            return;
        }

        saving = true;
        error = '';

        try {
            const response = await fetch('/api/reading', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    url: normalized,
                    title: title.trim()
                })
            });

            if (response.status === 409) {
                error = 'This article is already saved.';
                return;
            }

            if (!response.ok) throw new Error('Save failed');

            url = '';
            title = '';
            adding = false;
            await refresh();
        } catch {
            error = 'Could not save the article.';
        } finally {
            saving = false;
        }
    }

    async function toggleRead(article: Article) {
        try {
            const response = await fetch(`/api/reading/${article.id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ is_read: !article.is_read })
            });

            if (!response.ok) throw new Error('Update failed');

            const updated: Article = await response.json();

            articles = articles.map(a =>
                a.id === updated.id ? updated : a
            );
            error = '';
        } catch {
            error = 'Could not update the article.';
        }
    }

    async function remove(article: Article) {
        if (!confirm(`Remove "${article.title}"?`)) return;

        try {
            const response = await fetch(`/api/reading/${article.id}`, {
                method: 'DELETE'
            });

            if (!response.ok) throw new Error('Delete failed');

            articles = articles.filter(a => a.id !== article.id);
            error = '';
        } catch {
            error = 'Could not remove the article.';
        }
    }

    function hostname(value: string) {
        try {
            return new URL(value).hostname.replace(/^www\./, '');
        } catch {
            return value;
        }
    }
</script>

<section class="reading">
    <div class="reading-header">
        <div class="heading">
            <BookOpen size={18} strokeWidth={1.7} />
            <span>READING LIST</span>
            <span class="count">{unread} UNREAD</span>
        </div>

        <div class="actions">
            <button onclick={refresh} aria-label="Refresh reading list">
                <RefreshCw size={17} />
            </button>
            <button class="add-button" onclick={() => adding = !adding}>
                <Plus size={16} />
                Add article
            </button>
        </div>
    </div>

    {#if adding}
        <form class="add-form" onsubmit={save}>
            <input
                    bind:value={url}
                    placeholder="Paste an article URL..."
                    aria-label="Article URL"
                    required
            />
            <input
                    bind:value={title}
                    placeholder="Title (leave blank to auto-detect)"
                    aria-label="Article title"
            />
            <button type="submit" disabled={saving}>
                {saving ? 'Saving...' : 'Save'}
            </button>
        </form>
    {/if}

    {#if error}
        <div class="error" role="alert">{error}</div>
    {/if}

    {#if !initialized}
        <p class="empty">Loading your reading list...</p>
    {:else if articles.length === 0}
        <div class="empty">
            <BookOpen size={24} strokeWidth={1.3} />
            <p>Your reading list is empty.</p>
            <span>Save something interesting for later.</span>
        </div>
    {:else}
        <div class="article-list">
            {#each visible as article (article.id)}
                <div class="article" class:read={article.is_read}>
                    <button
                            class="read-toggle"
                            aria-label={article.is_read ? 'Mark unread' : 'Mark read'}
                            onclick={() => toggleRead(article)}
                    >
                        {#if article.is_read}
                            <CircleCheck size={21} />
                        {:else}
                            <Circle size={21} />
                        {/if}
                    </button>

                    <div class="article-info">
                        <a
                                href={article.url}
                                target="_blank"
                                rel="noopener noreferrer"
                        >
                            {article.title}
                            <ArrowUpRight size={14} />
                        </a>
                        <small>{hostname(article.url)}</small>
                    </div>

                    <button
                            class="delete"
                            aria-label="Delete article"
                            onclick={() => remove(article)}
                    >
                        <Trash2 size={17} />
                    </button>
                </div>
            {/each}
        </div>

        {#if articles.length > 3}
            <button
                    class="show-all"
                    onclick={() => showAll = !showAll}
            >
                {showAll ? 'Show less' : `View all ${articles.length} articles`}
            </button>
        {/if}
    {/if}
</section>

<style>
    .reading {
        max-width: 1060px;
        margin: 42px auto 0;
        padding: 24px;
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 16px;
    }

    .reading-header, .heading, .actions {
        display: flex;
        align-items: center;
    }

    .reading-header {
        justify-content: space-between;
        gap: 12px;
    }

    .heading {
        gap: 12px;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 1.4px;
    }

    .heading svg {
        color: var(--highlight);
    }

    .count {
        color: var(--muted);
        font-size: 10px;
        font-weight: 500;
        letter-spacing: 0;
    }

    .actions { gap: 9px; }

    button {
        color: var(--muted);
        border: none;
        background: transparent;
    }

    .actions > button {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        padding: 10px;
        border-radius: 9px;
    }

    button:hover { color: var(--text); }

    .actions .add-button {
        background: var(--surface-hover);
        color: var(--text);
        font-size: 12px;
        padding: 10px 13px;
    }

    .add-form {
        display: flex;
        flex-wrap: wrap;
        gap: 9px;
        margin-top: 22px;
    }

    .add-form input {
        min-width: 150px;
        flex: 1;
        border: 1px solid var(--line);
        border-radius: 9px;
        background: var(--bg);
        color: var(--text);
        outline: none;
        padding: 12px;
        font: inherit;
        font-size: 12px;
    }

    .add-form button {
        padding: 10px 18px;
        border-radius: 9px;
        color: var(--bg);
        background: var(--highlight);
        font-weight: 700;
        font-size: 12px;
    }

    .add-form button:disabled { opacity: .5; }

    .article-list { margin-top: 17px; }

    .article {
        display: flex;
        gap: 14px;
        align-items: center;
        padding: 15px 2px;
        border-top: 1px solid var(--line);
    }

    .read-toggle {
        display: grid;
        place-items: center;
        padding: 4px;
        color: var(--highlight);
    }

    .article.read .article-info a {
        color: var(--muted);
        font-weight: 400;
    }

    .article-info { flex: 1; min-width: 0; }

    .article-info a {
        display: flex;
        align-items: center;
        gap: 7px;
        width: fit-content;
        max-width: 100%;
        color: var(--text);
        font-size: 13px;
        font-weight: 600;
        text-decoration: none;
        overflow-wrap: anywhere;
    }

    .article-info a:hover { color: var(--highlight); }

    .article-info small {
        display: block;
        margin-top: 5px;
        color: var(--muted);
        font-size: 11px;
    }

    .delete { padding: 8px; }
    .delete:hover { color: #df9393; }

    .empty {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 9px;
        min-height: 130px;
        color: var(--muted);
        text-align: center;
        font-size: 12px;
    }

    .empty p { margin: 0; }
    .empty span { opacity: .7; }

    .error {
        margin-top: 12px;
        color: #e49b9b;
        font-size: 12px;
    }

    .show-all {
        display: block;
        margin: 12px auto 0;
        padding: 8px;
        font-size: 12px;
    }

    @media (max-width: 540px) {
        .reading { padding: 17px; }
        .heading { gap: 7px; letter-spacing: .6px; }
        .actions { gap: 2px; }
        .actions .add-button { padding: 9px; }
        .add-form { flex-direction: column; }
    }
</style>
