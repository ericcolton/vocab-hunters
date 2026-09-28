// Server-provided data; see the #generator-data block in generator.html
const pageData = JSON.parse(document.getElementById('generator-data').textContent);
const themeConfig = pageData.themeConfig;
const worksheetParams = pageData.worksheetParams || null;

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

    // Shuffle phrases and start from beginning
    const shuffledPhrases = shuffleArray(loadingPhrases);
    currentPhraseIndex = 0;

    // Set initial phrase
    if (phraseText) {
        phraseText.textContent = shuffledPhrases[currentPhraseIndex];
    }

    // Dim the content behind
    document.body.classList.add('is-loading');

    // Show modal
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');

    // Rotate phrases every 2.5 seconds
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

    // Stop phrase rotation
    if (loadingPhraseInterval) {
        clearInterval(loadingPhraseInterval);
        loadingPhraseInterval = null;
    }

    // Undim the content
    document.body.classList.remove('is-loading');

    // Hide modal
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
}

function isCustomTheme() {
    const selectedId = document.getElementById('theme').value;
    return themeConfig[selectedId] && themeConfig[selectedId].key_name === 'user_specified';
}

function isUserTheme() {
    return document.getElementById('theme').value.startsWith('u--');
}

function isUserDataset() {
    return document.getElementById('datasource').value.startsWith('u--');
}

function changeTheme() {
    const selectedId = document.getElementById('theme').value;
    const currentTheme = themeConfig[selectedId];

    // Update Text
    document.getElementById('ui-title').innerText = currentTheme.title;
    document.getElementById('ui-subtitle').innerText = currentTheme.subtitle;

    // Reset classes and apply new theme class
    document.body.className = currentTheme.css_class;

    // Toggle custom theme textarea
    const customContainer = document.getElementById('custom-theme-container');
    if (customContainer) {
        customContainer.hidden = !isCustomTheme();
    }
}

function getPreferenceSnapshot() {
    return {
        theme: document.getElementById('theme').value,
        datasource: document.getElementById('datasource').value,
        section: document.getElementById('section').value,
        level: document.getElementById('level').value,
        model: document.getElementById('model').value
    };
}

function savePreferences() {
    try {
        localStorage.setItem('homeworkHeroPrefs', JSON.stringify(getPreferenceSnapshot()));
    } catch (_err) {
        // Ignore storage issues (private mode, quota, etc.)
    }
}

function loadPreferences() {
    try {
        const raw = localStorage.getItem('homeworkHeroPrefs');
        return raw ? JSON.parse(raw) : null;
    } catch (_err) {
        return null;
    }
}

function updateGenerateButton(hasEpisodes) {
    const button = document.getElementById('generate-btn');
    if (!button) {
        return;
    }
    const label = button.querySelector('.btn-label');
    if (label) {
        label.textContent = hasEpisodes ? 'Generate New Episode' : 'Generate Episode';
    }
}

let isGenerating = false;

function setGenerateLoading(isLoading) {
    const button = document.getElementById('generate-btn');
    if (!button) {
        return;
    }
    isGenerating = isLoading;
    button.classList.toggle('is-loading', isLoading);
    button.disabled = isLoading;
    button.setAttribute('aria-busy', isLoading ? 'true' : 'false');

    // Show/hide the loading modal
    if (isLoading) {
        showLoadingModal();
    } else {
        hideLoadingModal();
    }
}

async function handleGenerate(event) {
    event.preventDefault();
    if (isGenerating) {
        return;
    }

    // Validate custom theme text if custom theme is selected
    if (isCustomTheme()) {
        const customText = document.getElementById('custom_theme_text').value.trim();
        if (!customText) {
            alert('Please describe your custom world before generating.');
            return;
        }
    }

    const headerInput = document.getElementById('header_text');
    const footerInput = document.getElementById('footer_text');
    const answerKeyFooterInput = document.getElementById('answer_key_footer');
    const headerValue = (headerInput?.value.trim()) || (headerInput?.placeholder) || '';
    const footerValue = (footerInput?.value.trim()) || (footerInput?.placeholder) || '';
    const answerKeyFooterValue = (answerKeyFooterInput?.value.trim())
        || (answerKeyFooterInput?.placeholder)
        || '';
    const payload = {
        datasource: document.getElementById('datasource').value,
        theme: document.getElementById('theme').value,
        level: document.getElementById('level').value,
        model: document.getElementById('model').value,
        section: document.getElementById('section').value,
        header_text: headerValue,
        footer_text: footerValue,
        answer_key_footer: answerKeyFooterValue
    };

    if (isCustomTheme()) {
        payload.custom_theme_text = document.getElementById('custom_theme_text').value.trim();
    }

    setGenerateLoading(true);
    try {
        const response = await fetch('/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!response.ok) {
            const contentType = response.headers.get('content-type') || '';
            if (contentType.includes('application/json')) {
                const errorPayload = await response.json();
                throw new Error(errorPayload.error || 'Generate failed.');
            }
            throw new Error('Generate failed.');
        }
        // Logged-in user content: server points at the persisted copy
        const myWorksheetUrl = response.headers.get('X-My-Worksheet-Url');
        if (myWorksheetUrl?.startsWith('/my/worksheet?')) {
            window.location.href = myWorksheetUrl;
            return;
        }
        const worksheetId = response.headers.get('X-Worksheet-Id');
        if (worksheetId) {
            window.location.href = `/worksheet?id=${encodeURIComponent(worksheetId)}`;
            return;
        }
        // No worksheet ID (custom theme) — download PDF as blob
        const disposition = response.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="([^"]+)"/);
        const filename = match ? match[1] : 'custom-worksheet.pdf';
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
    } catch (_err) {
        // Optional: surface error state in a future UI block.
    } finally {
        setGenerateLoading(false);
    }
}

// Overlapping updateEpisodes() calls can resolve out of order; only the
// most recent request is allowed to touch the DOM.
let episodesRequestId = 0;
let episodesAbortController = null;

async function updateEpisodes() {
    const container = document.getElementById('episode-list-container');
    const list = document.getElementById('episode_list');
    if (!container || !list) {
        return;
    }
    const requestId = ++episodesRequestId;
    if (episodesAbortController) {
        episodesAbortController.abort();
        episodesAbortController = null;
    }
    // Hide episodes for custom/user content (browsed via /my/worksheets instead)
    if (isCustomTheme() || isUserTheme() || isUserDataset()) {
        container.hidden = true;
        list.innerHTML = '';
        updateGenerateButton(false);
        return;
    }
    const sourceDataset = document.getElementById('datasource').value;
    const theme = document.getElementById('theme').value;
    const readingLevel = document.getElementById('level').value;
    const model = document.getElementById('model').value;
    const section = document.getElementById('section').value;

    const params = new URLSearchParams({
        source_dataset: sourceDataset,
        theme: theme,
        reading_level: readingLevel,
        model: model,
        section: section
    });

    const controller = new AbortController();
    episodesAbortController = controller;
    try {
        const response = await fetch(`/episodes?${params.toString()}`, { signal: controller.signal });
        if (!response.ok) {
            throw new Error('Failed to fetch episodes.');
        }
        const payload = await response.json();
        if (requestId !== episodesRequestId) {
            return;
        }
        const episodes = payload.episodes || [];
        if (!episodes.length) {
            container.hidden = true;
            list.innerHTML = '';
            updateGenerateButton(false);
            return;
        }
        container.hidden = false;
        list.innerHTML = '';
        episodes.forEach((episode) => {
            const entry = document.createElement('li');
            entry.className = 'episode-list-item';

            const episodeNumber = episode.episode ?? episode;
            const episodeSubtitle = (episode && typeof episode === 'object' && episode.subtitle) ? episode.subtitle : '';
            entry.dataset.episode = `${episodeNumber}`;
            entry.dataset.subtitle = episodeSubtitle;

            const pill = document.createElement('span');
            pill.className = 'episode-pill';
            pill.textContent = `#${episodeNumber}`;
            entry.appendChild(pill);

            const content = document.createElement('div');
            content.className = 'episode-title';
            if (episodeSubtitle) {
                content.textContent = episodeSubtitle;
            }

            entry.appendChild(content);

            const worksheetId = (episode && typeof episode === 'object') ? episode.worksheet_id : null;
            if (worksheetId) {
                const worksheetUrl = `/worksheet?id=${encodeURIComponent(worksheetId)}`;
                entry.style.cursor = 'pointer';
                entry.addEventListener('click', () => { window.location.href = worksheetUrl; });

                const previewLink = document.createElement('a');
                previewLink.className = 'episode-preview-link';
                previewLink.href = worksheetUrl;
                previewLink.textContent = 'View';
                entry.appendChild(previewLink);
            }

            list.appendChild(entry);
        });
        updateGenerateButton(true);
    } catch (_err) {
        if (requestId !== episodesRequestId) {
            return;
        }
        container.hidden = true;
        list.innerHTML = '';
        updateGenerateButton(false);
    }
}

// Rebuilds the section options for the selected dataset, restoring
// preferredSection when the new dataset offers it, then refreshes episodes once.
async function changeSourceDataset(preferredSection) {
    const selectedId = document.getElementById('datasource').value;
    const sectionSelect = document.getElementById('section');
    try {
        const response = await fetch(`/sections/${encodeURIComponent(selectedId)}`);
        const payload = await response.json();
        if (response.ok) {
            sectionSelect.innerHTML = '';
            (payload.sections || []).forEach((num) => {
                const option = document.createElement('option');
                option.value = num;
                option.textContent = num;
                sectionSelect.appendChild(option);
            });
        }
    } catch (_err) {
        // Keep existing sections if the request fails.
    }
    if (preferredSection != null && preferredSection !== '') {
        const wanted = String(preferredSection);
        if (Array.from(sectionSelect.options).some((opt) => opt.value === wanted)) {
            sectionSelect.value = wanted;
        }
    }
    updateEpisodes();
}

window.addEventListener('DOMContentLoaded', () => {
    const themeSelect = document.getElementById('theme');
    const sourceSelect = document.getElementById('datasource');
    const sectionSelect = document.getElementById('section');
    const levelSelect = document.getElementById('level');
    const modelSelect = document.getElementById('model');
    const generateForm = document.querySelector('.panel-form');

    if (worksheetParams) {
        // Pre-populate from worksheet URL params
        if (worksheetParams.theme) {
            themeSelect.value = worksheetParams.theme;
            changeTheme();
        }
        if (worksheetParams.source_dataset) {
            sourceSelect.value = worksheetParams.source_dataset;
        }
        if (worksheetParams.reading_level) {
            levelSelect.value = worksheetParams.reading_level;
        }
        if (worksheetParams.model) {
            modelSelect.value = worksheetParams.model;
        }

        changeSourceDataset(worksheetParams.section);
    } else {
        const saved = loadPreferences();
        if (saved) {
            if (saved.theme) {
                themeSelect.value = saved.theme;
                changeTheme();
            }
            if (saved.datasource) {
                sourceSelect.value = saved.datasource;
            }
            if (saved.level) {
                levelSelect.value = saved.level;
            }
            if (saved.model) {
                modelSelect.value = saved.model;
            }
        }

        changeSourceDataset(saved?.section);
    }

    themeSelect.addEventListener('change', () => {
        changeTheme();
        savePreferences();
        updateEpisodes();
    });
    sourceSelect.addEventListener('change', () => {
        changeSourceDataset().then(savePreferences);
    });
    levelSelect.addEventListener('change', () => {
        savePreferences();
        updateEpisodes();
    });
    sectionSelect.addEventListener('change', () => {
        savePreferences();
        updateEpisodes();
    });
    modelSelect.addEventListener('change', () => {
        savePreferences();
        updateEpisodes();
    });

    if (generateForm) {
        generateForm.addEventListener('submit', handleGenerate);
    }
});
