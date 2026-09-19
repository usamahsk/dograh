const crypto = require('crypto');
const axios = require('axios');

class LogAnalyticsClient {
    constructor(workspaceId, sharedKey, logType = 'AzureDiagnostics') {
        this.workspaceId = workspaceId;
        this.sharedKey = sharedKey;
        this.logType = logType;
    }

    /**
     * Sends logs to Azure Log Analytics.
     * @param logs An array of log entries to send.
     */
    async sendLogsToAzure(logs) {
        const customerId = this.workspaceId;
        const body = JSON.stringify(logs);
        const method = 'POST';
        const contentType = 'application/json';
        const resource = '/api/logs';
        const rfc1123Date = new Date().toUTCString();

        const stringToHash = `POST\n${Buffer.byteLength(body)}\napplication/json\nx-ms-date:${rfc1123Date}\n/api/logs`;
        const hashedString = crypto
            .createHmac('sha256', Buffer.from(this.sharedKey, 'base64'))
            .update(stringToHash)
            .digest('base64');

        const signature = `SharedKey ${customerId}:${hashedString}`;

        const headers = {
            'Content-Type': contentType,
            'Authorization': signature,
            'Log-Type': this.logType,
            'x-ms-date': rfc1123Date,
            'time-generated-field': 'time', // Optional: Define the time field name
        };

        const url = `https://${this.workspaceId}.ods.opinsights.azure.com${resource}?api-version=2016-04-01`;

        try {
            const response = await axios.post(url, body, { headers });
            // console.log('Response Status:', response.status);
            if (response.status === 200) {
                //console.log('Logs successfully sent to Azure Log Analytics.');
            } else {
                console.error('Failed to send logs:', response.data);
            }
        } catch (error) {
            console.error('Error sending logs:', error.message);
        }
    }
}