
<script lang="ts">
    import { onMount } from 'svelte';
    import {
        Archive, ArchiveRestore, ArrowUpRight,
        BookOpen, Check, Circle, CircleCheck,
        Pencil, Plus, RefreshCw, Search,
        Trash2, X
    } from '@lucide/svelte';

    type Article = {
        id: string;
        title: string;
        url: string;
        is_read: boolean;
        saved_at: string;
        archived_at: string | null;
    };

    type Tab = 'queue' | 'archive';

    let articles = $state<Article[]>([]);
    let tab = $state<Tab>('queue');
    let search = $state('');
    let loaded = $state(false);
    let error = $state('');

    let adding = $state(false);
    let saving = $state(false);
    let showAll = $state(false);

    let url = $state('');
    let title = $state('');

    let editingId = $state<string | null>(null);
    let titleDraft = $state('');
    let busyId = $state<string | null>(null);

    const queue = $derived(
        articles.filter(a => a.archived_at === null)
    );

    const archive = $derived(
        articles
            .filter(a => a.archived_at !== null)
            .sort((a, b) =>
                (b.archived_at ?? '').localeCompare(a.archived_at ?? '')
            )
    );

    const unread = $derived(
        queue.filter(a => !a.is_read).length
    );

    const filtered = $derived(
        (tab === 'queue' ? queue : archive).filter(a =>
            `${a.title} ${hostname(a.url)}`
                .toLowerCase()
                .includes(search.toLowerCase().trim())
        )
    );

    const visible = $derived(
        tab === 'archive' || showAll || search.trim()
            ? filtered
            : filtered.slice(0, 3)
    );

    function hostname(value: string): string {
        try {
            return new URL(value).hostname.replace(/^www\./, '');
        } catch {
            return value;
        }
    }

    function switchTab(next: Tab) {
        tab = next;
        showAll = false;
        editingId = null;
        error = '';
    }

    async function refresh() {
        try {
            const response = await fetch('/api/reading');

            if (!response.ok) throw new Error();

            articles = await response.json();
            error = '';
        } catch {
            error = 'Could not load the reading list.';
        } finally {
            loaded = true;
        }
    }

    onMount(() => {
        void refresh();

        const onVisible = () => {
            if (!document.hidden) void refresh();
        };

        const onFocus = () => void refresh();

        document.addEventListener('visibilitychange', onVisible);
        window.addEventListener('focus', onFocus);

        return () => {
            document.removeEventListener('visibilitychange', onVisible);
            window.removeEventListener('focus', onFocus);
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
                throw new Error();
            }
        } catch {
            error = 'Enter a valid HTTP or HTTPS URL.';
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

            if (!response.ok) throw new Error();

            url = '';
            title = '';
            adding = false;
            tab = 'queue';

            await refresh();
        } catch {
            error = 'Could not save the article.';
        } finally {
            saving = false;
        }
    }

    async function patch(
        article: Article,
        changes: Record<string, unknown>
    ): Promise<boolean> {
        if (busyId) return false;

        busyId = article.id;

        try {
            const response = await fetch(
                `/api/reading/${article.id}`,
                {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(changes)
                }
            );

            if (!response.ok) throw new Error();

            const updated: Article = await response.json();

            articles = articles.map(a =>
                a.id === updated.id ? updated : a
            );

            error = '';
            return true;
        } catch {
            error = 'Could not update the article.';
            return false;
        } finally {
            busyId = null;
        }
    }

    async function toggleRead(article: Article) {
        await patch(article, {
            is_read: !article.is_read
        });
    }

    async function toggleArchive(article: Article) {
        await patch(article, {
            archived: article.archived_at === null
        });
    }

    function startEditing(article: Article) {
        editingId = article.id;
        titleDraft = article.title;
    }

    async function saveTitle(article: Article) {
        const newTitle = titleDraft.trim();

        if (!newTitle) {
            error = 'Article title cannot be empty.';
            return;
        }

        const success = await patch(article, {
            title: newTitle
        });

        if (success) editingId = null;
    }

    async function remove(article: Article) {
        if (!confirm(`Permanently delete "${article.title}"?`)) {
            return;
        }

        try {
            const response = await fetch(
                `/api/reading/${article.id}`,
                { method: 'DELETE' }
            );

            if (!response.ok) throw new Error();

            articles = articles.filter(a => a.id !== article.id);
            error = '';
        } catch {
            error = 'Could not delete the article.';
        }
    }
</script>

<section class="reading">
    <header class="reading-header">
        <div class="heading">
            <BookOpen size={19} strokeWidth={1.7} />
            <span>READING LIST</span>
            <span class="count">{unread} UNREAD</span>
        </div>

        <div class="actions">
            <button
                    class="icon-action"
                    onclick={() => refresh()}
                    aria-label="Refresh"
                    title="Refresh reading list"
            >
                <RefreshCw size={17} />
            </button>

            <button
                    class="add-button"
                    onclick={() => adding = !adding}
            >
                <Plus size={16} />
                Add article
            </button>
        </div>
    </header>

    <div class="reading-toolbar">
        <div class="tabs" role="tablist" aria-label="Reading views">
            <button
                    class:active={tab === 'queue'}
                    role="tab"
                    aria-selected={tab === 'queue'}
                    onclick={() => switchTab('queue')}
            >
                Queue <span>{queue.length}</span>
            </button>

            <button
                    class:active={tab === 'archive'}
                    role="tab"
                    aria-selected={tab === 'archive'}
                    onclick={() => switchTab('archive')}
            >
                Archive <span>{archive.length}</span>
            </button>
        </div>

        <div class="reading-search">
            <Search size={16} />
            <input
                    bind:value={search}
                    aria-label="Search saved articles"
                    placeholder="Search articles..."
            />
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
                    maxlength="200"
            />
            <button type="submit" disabled={saving}>
                {saving ? 'Saving...' : 'Save'}
            </button>
        </form>
    {/if}

    {#if error}
        <p class="error" role="alert">{error}</p>
    {/if}

    {#if !loaded}
        <div class="empty">Loading your articles...</div>
    {:else if filtered.length === 0}
        <div class="empty">
            {#if tab === 'archive'}
                <Archive size={27} strokeWidth={1.3} />
                <p>No archived articles.</p>
                <small>Articles you archive will appear here.</small>
            {:else}
                <BookOpen size={27} strokeWidth={1.3} />
                <p>{search ? 'No matching articles.' : 'Your queue is empty.'}</p>
                <small>Save something interesting for later.</small>
            {/if}
        </div>
    {:else}
        <div class="article-list">
            {#each visible as article (article.id)}
                <div class="article" class:read={article.is_read}>
                    <button
                            class="icon-action read-toggle"
                            title={article.is_read ? 'Mark unread' : 'Mark read'}
                            aria-label={article.is_read ? 'Mark unread' : 'Mark read'}
                            disabled={busyId === article.id}
                            onclick={() => toggleRead(article)}
                    >
                        {#if article.is_read}
                            <CircleCheck size={21} />
                        {:else}
                            <Circle size={21} />
                        {/if}
                    </button>

                    <div class="article-info">
                        {#if editingId === article.id}
                            <form
                                    class="title-form"
                                    onsubmit={(event) => {
                  event.preventDefault();
                  void saveTitle(article);
                }}
                            >
                                <input
                                        bind:value={titleDraft}
                                        maxlength="200"
                                        required
                                        aria-label="New article title"
                                />

                                <button
                                        type="submit"
                                        disabled={busyId === article.id}
                                        title="Save title"
                                >
                                    <Check size={17} />
                                </button>

                                <button
                                        type="button"
                                        onclick={() => editingId = null}
                                        title="Cancel editing"
                                >
                                    <X size={17} />
                                </button>
                            </form>
                        {:else}
                            <a
                                    href={article.url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                            >
                                {article.title}
                                <ArrowUpRight size={14} />
                            </a>
                        {/if}

                        <small>{hostname(article.url)}</small>
                    </div>

                    <div class="article-actions">
                        <button
                                class="icon-action"
                                title="Edit title"
                                aria-label="Edit title"
                                onclick={() => startEditing(article)}
                        >
                            <Pencil size={16} />
                        </button>

                        <button
                                class="icon-action"
                                title={tab === 'queue' ? 'Archive' : 'Restore'}
                                aria-label={tab === 'queue' ? 'Archive' : 'Restore'}
                                disabled={busyId === article.id}
                                onclick={() => toggleArchive(article)}
                        >
                            {#if tab === 'queue'}
                                <Archive size={17} />
                            {:else}
                                <ArchiveRestore size={17} />
                            {/if}
                        </button>

                        <button
                                class="icon-action delete"
                                title="Delete permanently"
                                aria-label="Delete permanently"
                                onclick={() => remove(article)}
                        >
                            <Trash2 size={17} />
                        </button>
                    </div>
                </div>
            {/each}
        </div>

        {#if tab === 'queue' && !showAll && !search.trim() && filtered.length > 3}
            <button class="show-all" onclick={() => showAll = true}>
                View all {filtered.length} articles
            </button>
        {:else if tab === 'queue' && showAll && !search.trim() && filtered.length > 3}
            <button class="show-all" onclick={() => showAll = false}>
                Show less
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

    .reading-header,
    .heading,
    .actions,
    .reading-toolbar,
    .article,
    .article-actions {
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

    button {
        border: none;
        background: transparent;
        color: var(--muted);
        cursor: pointer;
    }

    button:hover {
        color: var(--text);
    }

    button:disabled {
        opacity: .4;
        cursor: default;
    }

    .actions {
        gap: 8px;
    }

    .icon-action {
        display: grid;
        place-items: center;
        flex-shrink: 0;
        width: 32px;
        height: 32px;
        border-radius: 8px;
    }

    .icon-action:hover {
        background: var(--surface-hover);
    }

    .add-button {
        display: flex;
        align-items: center;
        gap: 7px;
        padding: 10px 13px;
        background: var(--surface-hover);
        color: var(--text);
        border-radius: 9px;
        font-size: 12px;
    }

    .reading-toolbar {
        justify-content: space-between;
        gap: 16px;
        margin-top: 24px;
        padding-bottom: 13px;
        border-bottom: 1px solid var(--line);
    }

    .tabs {
        display: flex;
        gap: 5px;
    }

    .tabs button {
        display: flex;
        align-items: center;
        gap: 9px;
        padding: 9px 13px;
        border-radius: 9px;
        font-size: 12px;
        transition: background .2s, color .2s;
    }

    .tabs button.active {
        background: var(--surface-hover);
        color: var(--text);
        font-weight: 600;
    }

    .tabs span {
        font-size: 10px;
        opacity: .6;
    }

    .reading-search {
        display: flex;
        align-items: center;
        gap: 8px;
        max-width: 235px;
        color: var(--muted);
    }

    .reading-search input {
        width: 100%;
        min-width: 0;
        background: transparent;
        color: var(--text);
        border: none;
        outline: none;
        font-size: 12px;
    }

    .reading-search input::placeholder,
    .add-form input::placeholder {
        color: var(--muted);
    }

    .add-form {
        display: flex;
        flex-wrap: wrap;
        gap: 9px;
        margin-top: 18px;
    }

    .add-form input,
    .title-form input {
        flex: 1;
        min-width: 120px;
        padding: 11px;
        background: var(--bg);
        border: 1px solid var(--line);
        border-radius: 8px;
        color: var(--text);
        font: inherit;
        font-size: 12px;
        outline: none;
    }

    .add-form button[type="submit"] {
        padding: 10px 18px;
        background: var(--highlight);
        color: var(--bg);
        border-radius: 8px;
        font-size: 12px;
        font-weight: 700;
    }

    .article {
        gap: 12px;
        padding: 15px 0;
        border-bottom: 1px solid var(--line);
    }

    .article:last-child {
        border-bottom: none;
    }

    .read-toggle {
        color: var(--highlight);
    }

    .article-info {
        min-width: 0;
        flex: 1;
    }

    .article-info a {
        display: flex;
        align-items: center;
        gap: 7px;
        width: fit-content;
        max-width: 100%;
        color: var(--text);
        text-decoration: none;
        font-size: 13px;
        font-weight: 600;
        overflow-wrap: anywhere;
    }

    .article-info a:hover {
        color: var(--highlight);
    }

    .article.read .article-info a {
        color: var(--muted);
        font-weight: 400;
    }

    .article-info small {
        display: block;
        margin-top: 5px;
        color: var(--muted);
        font-size: 11px;
    }

    .article-actions {
        gap: 3px;
    }

    .article-actions .delete:hover {
        color: #df9393;
    }

    .title-form {
        display: flex;
        align-items: center;
        gap: 5px;
    }

    .title-form button {
        display: grid;
        place-items: center;
        padding: 7px;
    }

    .empty {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 10px;
        min-height: 145px;
        color: var(--muted);
        text-align: center;
        font-size: 12px;
    }

    .empty p {
        margin: 0;
    }

    .empty small {
        opacity: .7;
    }

    .show-all {
        display: block;
        margin: 13px auto 0;
        padding: 8px;
        font-size: 12px;
    }

    .error {
        margin: 13px 0;
        color: #df9393;
        font-size: 12px;
    }

    @media (max-width: 640px) {
        .reading {
            padding: 17px;
        }

        .reading-toolbar {
            flex-direction: column;
            align-items: stretch;
            gap: 15px;
        }

        .reading-search {
            max-width: none;
            padding: 9px 10px;
            border: 1px solid var(--line);
            border-radius: 9px;
        }

        .article {
            gap: 7px;
        }

        .article-actions {
            gap: 0;
        }

        .article-actions .icon-action {
            width: 29px;
        }

        .add-form {
            flex-direction: column;
        }
    }
</style>
