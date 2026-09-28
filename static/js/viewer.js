// Server-provided data; see the #viewer-data block in viewer.html
const pageData = JSON.parse(document.getElementById('viewer-data').textContent);
const viewerData = pageData.viewer;

// Silly loading phrases
const loadingPhrases = [
    "Warming up the AI brain",
    "Teaching vocabulary to robots",
    "Consulting the word wizards",
    "Brewing a fresh batch of sentences",
    "Asking the thesaurus for directions",
    "Sharpening the digital pencils",
    "Convincing electrons to form words",
    "Summoning the grammar gremlins",
    "Downloading extra creativity",
    "Untangling the syntax spaghetti",
    "Polishing the vocabulary gems",
    "Waking up the sentence elves",
    "Calibrating the fun-o-meter",
    "Herding the alphabet cats",
    "Spinning up the word tornado",
    "Feeding the hungry algorithms",
    "Consulting ancient dictionaries",
    "Negotiating with the punctuation union",
    "Assembling the sentence squad",
    "Loading extra awesome sauce",
    "Tuning the linguistic engines",
    "Asking nicely for good answers",
    "Bribing the AI with cookies",
    "Stretching the neural networks",
    "Doing vocabulary jumping jacks"
];

let loadingPhraseInterval = null;
let currentPhraseIndex = 0;

function shuffleArray(array) {
    const shuffled = [...array];
    for (let i = shuffled.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }
    return shuffled;
}

function showLoadingModal() {
    const modal = document.getElementById('loading-modal');
    const phraseText = document.getElementById('loading-phrase-text');
    if (!modal) return;

    const shuffledPhrases = shuffleArray(loadingPhrases);
    currentPhraseIndex = 0;

    if (phraseText) {
        phraseText.textContent = shuffledPhrases[currentPhraseIndex];
    }

    document.body.classList.add('is-loading');
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');

    loadingPhraseInterval = setInterval(() => {
        currentPhraseIndex = (currentPhraseIndex + 1) % shuffledPhrases.length;
        if (phraseText) {
            phraseText.style.animation = 'none';
            phraseText.offsetHeight; // Trigger reflow
            phraseText.style.animation = 'fadePhrase 0.4s ease-in-out';
            phraseText.textContent = shuffledPhrases[currentPhraseIndex];
        }
    }, 2500);
}

function hideLoadingModal() {
    const modal = document.getElementById('loading-modal');
    if (!modal) return;

    if (loadingPhraseInterval) {
        clearInterval(loadingPhraseInterval);
        loadingPhraseInterval = null;
    }

    document.body.classList.remove('is-loading');
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
}

// ── Page State ──
const SEED_BITS = 8;
const MAX_SEED = (1 << SEED_BITS) - 1;

const PREV_ARROW_SVG = '<svg viewBox="0 0 24 24"><path d="M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z"/></svg>';
const NEXT_ARROW_SVG = '<svg viewBox="0 0 24 24"><path d="M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z"/></svg>';

const pageState = {
    worksheetId: viewerData.worksheet_id,
    episode: viewerData.params.seed,
    episodes: viewerData.episodes,
    prevEp: null,
    nextEp: null,
    nextIsGenerate: viewerData.next_is_generate,
    nextGenerateEpisode: viewerData.next_generate_episode,
};

// ── Mode-aware URL builders ──
// Global worksheets are addressed by packed worksheet IDs; user-owned
// worksheets (mode 'user') by explicit params on the /my/* routes.
const isUserMode = viewerData.mode === 'user';

function myParams(episode) {
    return new URLSearchParams({
        source_dataset: viewerData.params.source_dataset,
        theme: viewerData.params.theme,
        reading_level: viewerData.params.reading_level,
        section: String(viewerData.params.section),
        model: viewerData.params.model,
        episode: String(episode),
    });
}

function episodePageUrl(ep) {
    if (isUserMode) return `/my/worksheet?${myParams(ep.episode).toString()}`;
    return `/worksheet?id=${encodeURIComponent(ep.worksheet_id)}`;
}

function currentPdfUrl() {
    if (isUserMode) return `/my/worksheet_pdf?${myParams(pageState.episode).toString()}`;
    return `/worksheet_pdf?id=${encodeURIComponent(pageState.worksheetId)}`;
}

// ── UI Update Functions ──
function updateIframe() {
    const frame = document.getElementById('pdf-frame');
    if (frame) frame.src = currentPdfUrl();
}

function updateHeader() {
    const el = document.querySelector('.top-bar-episode');
    if (el) el.innerHTML = `Episode ${pageState.episode} &middot; Section ${viewerData.params.section}`;
}

function buildPdfFilename(episode) {
    const config = pageData.config;
    const params = viewerData.params;
    let sourceAbbr = params.source_dataset;
    for (const ds of config.data_sources) {
        if (ds.id === params.source_dataset) {
            sourceAbbr = ds.title_abbr || ds.key_name || params.source_dataset;
            break;
        }
    }
    let themeAbbr = params.theme;
    for (const t of config.themes) {
        if (t.id === params.theme) {
            themeAbbr = t.title_abbr || t.key_name || params.theme;
            break;
        }
    }
    const sanitize = s => s.replace(/ /g, '_').replace(/\//g, '-');
    return `${sanitize(sourceAbbr)}-${sanitize(themeAbbr)}-S${params.section}-E${episode}.pdf`;
}

function updateDownloadLink() {
    const link = document.querySelector('.top-bar-right .bar-btn');
    if (link) {
        link.href = currentPdfUrl();
        link.download = buildPdfFilename(pageState.episode);
    }
}

function handlePrint() {
    const url = currentPdfUrl();

    // Remove any previous print frame
    const existing = document.getElementById('print-frame');
    if (existing) existing.remove();

    const frame = document.createElement('iframe');
    frame.id = 'print-frame';
    frame.style.position = 'fixed';
    frame.style.width = '0';
    frame.style.height = '0';
    frame.style.border = '0';
    frame.src = url;

    frame.onload = () => {
        try {
            frame.contentWindow.focus();
            frame.contentWindow.print();
        } catch (_e) {
            // Fallback: open in new tab for manual printing
            window.open(url, '_blank');
        }
    };

    document.body.appendChild(frame);
}

function updateNavArrows() {
    const mainArea = document.querySelector('.main-area');
    if (!mainArea) return;

    const children = mainArea.children;
    const prevArrow = children[0];
    const nextArrow = children[children.length - 1];

    // Rebuild prev arrow
    let newPrev;
    if (pageState.prevEp) {
        newPrev = document.createElement('a');
        newPrev.href = episodePageUrl(pageState.prevEp);
        newPrev.className = 'nav-arrow';
        newPrev.title = 'Previous episode';
    } else {
        newPrev = document.createElement('span');
        newPrev.className = 'nav-arrow disabled';
    }
    newPrev.innerHTML = PREV_ARROW_SVG;
    mainArea.replaceChild(newPrev, prevArrow);

    // Rebuild next arrow
    let newNext;
    if (pageState.nextEp) {
        newNext = document.createElement('a');
        newNext.href = episodePageUrl(pageState.nextEp);
        newNext.className = 'nav-arrow';
        newNext.title = 'Next episode';
    } else if (pageState.nextIsGenerate) {
        newNext = document.createElement('button');
        newNext.id = 'generate-next-btn';
        newNext.className = 'nav-arrow generate-next';
        newNext.title = `Generate Episode ${pageState.nextGenerateEpisode}`;
        newNext.addEventListener('click', handleGenerateClick);
    } else {
        newNext = document.createElement('span');
        newNext.className = 'nav-arrow disabled';
    }
    newNext.innerHTML = NEXT_ARROW_SVG;
    mainArea.replaceChild(newNext, nextArrow);
}

function updatePaginationBar() {
    const bar = document.getElementById('pagination-bar');
    if (!bar) return;

    const episodes = pageState.episodes;
    const current = pageState.episode;
    const total = episodes.length;
    let html = '';

    if (total <= 7) {
        for (const ep of episodes) {
            if (ep.episode === current) {
                html += `<span class="page-btn active">${ep.episode}</span>`;
            } else {
                html += `<a href="${episodePageUrl(ep)}" class="page-btn">${ep.episode}</a>`;
            }
        }
    } else {
        const ci = episodes.findIndex(ep => ep.episode === current);
        const indices = new Set([0, total - 1]);
        for (const offset of [-1, 0, 1]) {
            const idx = ci + offset;
            if (idx >= 0 && idx < total) indices.add(idx);
        }
        const sorted = Array.from(indices).sort((a, b) => a - b);

        let prevIdx = -2;
        for (const idx of sorted) {
            if (idx - prevIdx > 1) {
                html += '<span class="page-ellipsis">&hellip;</span>';
            }
            const ep = episodes[idx];
            if (ep.episode === current) {
                html += `<span class="page-btn active">${ep.episode}</span>`;
            } else {
                html += `<a href="${episodePageUrl(ep)}" class="page-btn">${ep.episode}</a>`;
            }
            prevIdx = idx;
        }
    }

    bar.innerHTML = html;
}

// ── Generate Handler ──
async function handleGenerateClick() {
    const btn = document.getElementById('generate-next-btn');
    if (!btn || btn.disabled) return;

    showLoadingModal();
    btn.disabled = true;

    const payload = {
        source_dataset: viewerData.params.source_dataset,
        theme: viewerData.params.theme,
        level: viewerData.params.reading_level,
        model: viewerData.params.model,
        section: viewerData.params.section,
        presentation_metadata: {
            header: '{theme} - Section {section}',
            footer: 'Page {current_page} of {total_pages}',
            answer_key_footer: 'Fountas & Pinnell Level {reading_level}',
        },
    };

    try {
        // Generate the new episode on the server
        const response = await fetch('/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!response.ok) throw new Error('Generate failed');

        // Fetch updated episodes list to get the new episode's worksheet ID
        const newEpisode = pageState.nextGenerateEpisode;
        await fetchAndUpdateEpisodes();

        const newEpEntry = pageState.episodes.find(ep => ep.episode === newEpisode);
        if (!newEpEntry) throw new Error('New episode not found in list');
        if (!isUserMode && !newEpEntry.worksheet_id) throw new Error('New episode not found in list');

        // Advance state to the new episode and recompute nav locally
        pageState.worksheetId = newEpEntry.worksheet_id || null;
        pageState.episode = newEpisode;
        recomputeNavState();

        // Update all UI elements in place
        updateIframe();
        updateHeader();
        updateDownloadLink();
        updateNavArrows();
        updatePaginationBar();

        // Update browser URL without full navigation
        history.pushState(null, '', episodePageUrl(newEpEntry));

        hideLoadingModal();
    } catch (err) {
        console.error('Generate failed:', err);
        hideLoadingModal();
        if (btn) btn.disabled = false;
    }
}

// ── Shared: recompute prev/next nav from current episodes + episode ──
function recomputeNavState() {
    const episodes = pageState.episodes;
    const currentIdx = episodes.findIndex(ep => ep.episode === pageState.episode);
    pageState.prevEp = currentIdx > 0 ? episodes[currentIdx - 1] : null;

    if (currentIdx >= 0 && currentIdx < episodes.length - 1) {
        pageState.nextEp = episodes[currentIdx + 1];
        pageState.nextIsGenerate = false;
        pageState.nextGenerateEpisode = null;
    } else {
        pageState.nextEp = null;
        const lastEp = episodes.length > 0 ? episodes[episodes.length - 1].episode : 0;
        if (lastEp < MAX_SEED) {
            pageState.nextIsGenerate = true;
            pageState.nextGenerateEpisode = lastEp + 1;
        } else {
            pageState.nextIsGenerate = false;
            pageState.nextGenerateEpisode = null;
        }
    }
}

// ── Shared: fetch episodes list from server and update nav state ──
async function fetchAndUpdateEpisodes() {
    const epParams = new URLSearchParams({
        source_dataset: viewerData.params.source_dataset,
        theme: viewerData.params.theme,
        reading_level: viewerData.params.reading_level,
        model: viewerData.params.model,
        section: String(viewerData.params.section),
    });
    const epResponse = await fetch((isUserMode ? '/my/episodes?' : '/episodes?') + epParams.toString());
    if (!epResponse.ok) throw new Error('Failed to fetch episodes');
    const epData = await epResponse.json();
    pageState.episodes = epData.episodes || [];
    recomputeNavState();
}

// ── Generate missing episode (QR code for uncached episode) ──
async function generateMissingEpisode() {
    showLoadingModal();
    try {
        // Trigger generation via worksheet_pdf (generates any specific episode)
        const pdfResponse = await fetch(currentPdfUrl());
        if (!pdfResponse.ok) throw new Error('PDF generation failed');

        // Load the PDF into the iframe using a blob URL
        const pdfBytes = await pdfResponse.arrayBuffer();
        const blob = new Blob([pdfBytes], { type: 'application/pdf' });
        const frame = document.getElementById('pdf-frame');
        if (frame) frame.src = URL.createObjectURL(blob);

        // Fetch updated episodes and update navigation state
        await fetchAndUpdateEpisodes();

        updateNavArrows();
        updatePaginationBar();
        hideLoadingModal();
    } catch (err) {
        console.error('Episode generation failed:', err);
        hideLoadingModal();
    }
}

document.getElementById('print-btn').addEventListener('click', handlePrint);

// Attach handler to initial generate button (if present)
const initialGenerateBtn = document.getElementById('generate-next-btn');
if (initialGenerateBtn) {
    initialGenerateBtn.addEventListener('click', handleGenerateClick);
}

// If the episode doesn't exist yet, generate it on page load
if (!viewerData.episode_exists) {
    generateMissingEpisode();
}

// Handle browser back/forward after pushState
window.addEventListener('popstate', () => {
    window.location.reload();
});
