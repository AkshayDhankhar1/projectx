/**
 * app.js — Store Intelligence Dashboard JavaScript
 * 
 * This script powers the live dashboard by:
 * 1. Connecting to the API via WebSocket for real-time event updates
 * 2. Polling the API endpoints periodically for metric refreshes
 * 3. Rendering the funnel chart, heatmap, and anomaly feed
 * 4. Animating metric value changes with smooth transitions
 * 
 * How it works:
 * - On page load: fetch initial data from all API endpoints
 * - Every 5 seconds: refresh metrics, funnel, heatmap, anomalies
 * - On WebSocket message: update the live event feed instantly
 */

// ============================================================
// Configuration
// ============================================================

// API base URL — when running via Docker Compose with nginx proxy,
// requests go through /api/. When running locally, direct to port 8000.
const API_BASE = window.location.port === '3000' 
    ? '/api'                          // Through nginx proxy
    : 'http://localhost:8000';        // Direct to API

// WebSocket URL
const WS_URL = window.location.port === '3000'
    ? `ws://${window.location.host}/ws`     // Through nginx proxy
    : 'ws://localhost:8000/ws';              // Direct

// How often to refresh metrics (milliseconds)
const REFRESH_INTERVAL = 5000;

// Maximum events to show in the live feed
const MAX_FEED_EVENTS = 50;

// Currently selected store
let currentStoreId = 'STORE_PRP_001';

// WebSocket connection reference
let ws = null;

// Event counter
let totalEventCount = 0;


// ============================================================
// Initialization — runs when the page loads
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
    console.log('Dashboard initializing...');
    
    // Set up store selector
    const selector = document.getElementById('store-selector');
    selector.addEventListener('change', (e) => {
        currentStoreId = e.target.value;
        refreshAll();
    });
    
    // Initial data load
    refreshAll();
    
    // Start periodic refresh
    setInterval(refreshAll, REFRESH_INTERVAL);
    
    // Connect WebSocket
    connectWebSocket();
});


// ============================================================
// WebSocket Connection — for real-time event updates
// ============================================================

function connectWebSocket() {
    try {
        ws = new WebSocket(WS_URL);
        
        ws.onopen = () => {
            console.log('WebSocket connected');
            const statusDot = document.getElementById('connection-status');
            statusDot.className = 'status-dot connected';
            statusDot.title = 'WebSocket connected — receiving live events';
        };
        
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            
            if (data.type === 'new_events') {
                // New events ingested — add to the live feed
                data.events.forEach(evt => addEventToFeed(evt));
                totalEventCount += data.count;
                document.getElementById('event-count').textContent = 
                    `${totalEventCount} events`;
                
                // Also refresh metrics since data changed
                refreshMetrics();
            }
        };
        
        ws.onclose = () => {
            console.log('WebSocket disconnected — reconnecting in 3s...');
            const statusDot = document.getElementById('connection-status');
            statusDot.className = 'status-dot disconnected';
            statusDot.title = 'WebSocket disconnected — reconnecting...';
            
            // Auto-reconnect after 3 seconds
            setTimeout(connectWebSocket, 3000);
        };
        
        ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };
        
    } catch (e) {
        console.error('WebSocket connection failed:', e);
        setTimeout(connectWebSocket, 5000);
    }
}


// ============================================================
// API Data Fetching
// ============================================================

async function fetchJSON(endpoint) {
    try {
        const response = await fetch(`${API_BASE}${endpoint}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return await response.json();
    } catch (error) {
        console.error(`Failed to fetch ${endpoint}:`, error);
        return null;
    }
}

async function refreshAll() {
    await Promise.all([
        refreshMetrics(),
        refreshFunnel(),
        refreshHeatmap(),
        refreshAnomalies(),
    ]);
}


// ============================================================
// Metrics Update
// ============================================================

async function refreshMetrics() {
    const data = await fetchJSON(`/stores/${currentStoreId}/metrics`);
    if (!data) return;
    
    updateMetricValue('metric-visitors', data.unique_visitors);
    updateMetricValue('metric-conversion', 
        `${(data.conversion_rate * 100).toFixed(1)}%`);
    updateMetricValue('metric-queue', data.current_queue_depth);
    updateMetricValue('metric-revenue', 
        `₹${data.total_revenue.toLocaleString('en-IN')}`);
    updateMetricValue('metric-abandonment', 
        `${(data.abandonment_rate * 100).toFixed(1)}%`);
}

function updateMetricValue(elementId, newValue) {
    const el = document.getElementById(elementId);
    const currentValue = el.textContent;
    const newStr = String(newValue);
    
    if (currentValue !== newStr) {
        el.textContent = newStr;
        // Flash green briefly to show the value changed
        el.classList.add('updated');
        setTimeout(() => el.classList.remove('updated'), 1000);
    }
}


// ============================================================
// Funnel Chart
// ============================================================

async function refreshFunnel() {
    const data = await fetchJSON(`/stores/${currentStoreId}/funnel`);
    if (!data || !data.stages) return;
    
    const maxCount = Math.max(...data.stages.map(s => s.count), 1);
    
    const barIds = ['funnel-entry-bar', 'funnel-zone-bar', 
                    'funnel-billing-bar', 'funnel-purchase-bar'];
    
    data.stages.forEach((stage, i) => {
        if (i < barIds.length) {
            const bar = document.getElementById(barIds[i]);
            const widthPct = Math.max((stage.count / maxCount) * 100, 5);
            bar.style.width = `${widthPct}%`;
            bar.querySelector('span').textContent = stage.count;
        }
    });
}


// ============================================================
// Heatmap
// ============================================================

async function refreshHeatmap() {
    const data = await fetchJSON(`/stores/${currentStoreId}/heatmap`);
    if (!data || !data.zones) return;
    
    const container = document.getElementById('heatmap-grid');
    
    if (data.zones.length === 0) {
        container.innerHTML = '<p class="empty-state">No zone data yet</p>';
        return;
    }
    
    container.innerHTML = '';
    
    data.zones.forEach(zone => {
        const cell = document.createElement('div');
        
        // Map normalized score (0-100) to heat level (0-4)
        const heatLevel = Math.min(Math.floor(zone.normalized_score / 25), 4);
        cell.className = `heatmap-cell heat-${heatLevel}`;
        
        // Format dwell time
        const dwellSec = (zone.avg_dwell_ms / 1000).toFixed(0);
        
        cell.innerHTML = `
            <div class="zone-name">${zone.zone_id}</div>
            <div class="zone-visits">${zone.visit_count}</div>
            <div class="zone-dwell">${dwellSec}s avg dwell</div>
        `;
        
        cell.title = `${zone.zone_id}: ${zone.visit_count} visits, ${dwellSec}s avg dwell (confidence: ${zone.data_confidence})`;
        
        container.appendChild(cell);
    });
}


// ============================================================
// Anomalies
// ============================================================

async function refreshAnomalies() {
    const data = await fetchJSON(`/stores/${currentStoreId}/anomalies`);
    if (!data) return;
    
    const container = document.getElementById('anomalies-list');
    
    if (!data.anomalies || data.anomalies.length === 0) {
        container.innerHTML = '<p class="empty-state">✅ No anomalies detected</p>';
        return;
    }
    
    container.innerHTML = '';
    
    data.anomalies.forEach(anomaly => {
        const item = document.createElement('div');
        item.className = `anomaly-item severity-${anomaly.severity}`;
        
        item.innerHTML = `
            <span class="anomaly-severity">${anomaly.severity}</span>
            <div>
                <div class="anomaly-text">${anomaly.description}</div>
                <div class="anomaly-action">💡 ${anomaly.suggested_action}</div>
            </div>
        `;
        
        container.appendChild(item);
    });
}


// ============================================================
// Live Event Feed
// ============================================================

function addEventToFeed(event) {
    const container = document.getElementById('event-feed');
    
    // Remove "Waiting for events..." message
    const emptyState = container.querySelector('.empty-state');
    if (emptyState) emptyState.remove();
    
    // Create event row
    const row = document.createElement('div');
    row.className = 'event-row';
    
    // Format timestamp to just time
    const time = new Date(event.timestamp).toLocaleTimeString('en-IN');
    
    row.innerHTML = `
        <span class="event-time">${time}</span>
        <span class="event-type ${event.event_type}">${event.event_type}</span>
        <span class="event-visitor">${event.visitor_id}</span>
        <span class="event-zone">${event.zone_id || '—'}</span>
        <span class="event-confidence">${(event.confidence * 100).toFixed(0)}%</span>
    `;
    
    // Add to top of feed (newest first)
    container.insertBefore(row, container.firstChild);
    
    // Limit feed length to avoid memory issues
    while (container.children.length > MAX_FEED_EVENTS) {
        container.removeChild(container.lastChild);
    }
}
