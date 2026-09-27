// Tailwind build configuration for platform/ops.css - the prebuilt stylesheet
// of the private Ops console. Same theme and pinned Tailwind as the app
// (tailwind.config.js); only the scanned files differ. The console used to load
// the in-browser compiler from /assets/vendor, which row 73 (OBS-11) removed,
// leaving Ops without any utility styles (OPS-ACCURACY-001).
// Rebuild with: node scripts/build-tailwind-css.js
const path = require('path');
const app = require('./tailwind.config.js');
const platform = path.resolve(__dirname, '..', '..', 'platform');

module.exports = {
    ...app,
    content: [
        path.join(platform, '*.html'),
        path.join(platform, 'js', '**', '*.js'),
    ],
};
