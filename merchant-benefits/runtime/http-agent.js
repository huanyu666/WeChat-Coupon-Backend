'use strict';

const https = require('https');

// Both benefit queries use the same Meituan endpoint. Keep TLS connections
// warm so the second request does not repeat DNS, TCP and TLS setup.
module.exports = new https.Agent({
    keepAlive: true,
    keepAliveMsecs: 1000,
    maxSockets: 32,
    maxFreeSockets: 8,
});
