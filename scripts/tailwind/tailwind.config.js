// Tailwind build configuration for the prebuilt frontend/css/tailwind.css
// (audit OBS-11). This is the theme the page used to hand to Tailwind's
// in-browser compiler in index.html; keep the two in step if it ever changes.
// Rebuild with: node scripts/build-tailwind-css.js
const path = require('path');
const frontend = path.resolve(__dirname, '..', '..', 'frontend');

module.exports = {
    content: [
        path.join(frontend, '*.html'),
        path.join(frontend, 'js', '**', '*.js'),
    ],
    darkMode: 'class',
    theme: {
        extend: {
            colors: {
                bgMain: '#070B14',
                sidebarBg: '#0A0F1D',
                cardBg: '#0D1322',
                cardHover: '#131C31',
                borderCol: '#172033',
                primary: '#436BEE',
                primaryHover: '#375BCC',
                textMain: '#E2E8F0',
                textSec: '#7C8BA1',
                success: '#1CA850',
                warning: '#EAB308',
                danger: '#D94141'
            },
            fontFamily: {
                sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
                // Tailwind's default mono stack plus the one-glyph Naira fallback (NAT-001, see base.css).
                mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', '"Liberation Mono"', '"Cauldra Currency"', '"Courier New"', 'monospace']
            }
        }
    }
};
