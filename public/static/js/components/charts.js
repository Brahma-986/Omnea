/**
 * Omnea SVG Chart Components (charts.js)
 * Lightweight, zero-dependency, accessible SVG charts.
 * Supports Bar Charts, Line Charts, Donut Gauges, and Sparklines.
 */

import { escapeHtml } from '../state.js';

export class ChartRenderer {
  /**
   * Render an accessible SVG Bar Chart
   * @param {Object} options
   * @param {HTMLElement} options.container
   * @param {Array<{ label: string, value: number }>} options.data
   * @param {string} options.title
   * @param {number} [options.height]
   */
  static renderBarChart({ container, data = [], title = 'Chart', height = 220 }) {
    if (!container) return;
    container.innerHTML = '';

    const width = 500;
    const padding = { top: 20, right: 20, bottom: 40, left: 50 };
    const chartWidth = width - padding.left - padding.right;
    const chartHeight = height - padding.top - padding.bottom;

    const maxValue = Math.max(...data.map(d => d.value), 10);
    const barWidth = Math.max(12, Math.floor(chartWidth / (data.length * 1.6)));
    const gap = (chartWidth - barWidth * data.length) / (data.length + 1);

    let barsSvg = '';
    let axesSvg = '';

    // Horizontal grid lines & Y labels (4 ticks)
    for (let i = 0; i <= 4; i++) {
      const yVal = Math.round((maxValue / 4) * i);
      const yPos = padding.top + chartHeight - (chartHeight / 4) * i;
      axesSvg += `
        <line class="chart-grid" x1="${padding.left}" y1="${yPos}" x2="${width - padding.right}" y2="${yPos}" />
        <text class="chart-axis-text" x="${padding.left - 8}" y="${yPos + 4}" text-anchor="end">${yVal}</text>
      `;
    }

    // Bars & X labels
    data.forEach((d, idx) => {
      const barHeight = Math.round((d.value / maxValue) * chartHeight);
      const xPos = Math.round(padding.left + gap + idx * (barWidth + gap));
      const yPos = Math.round(padding.top + chartHeight - barHeight);

      barsSvg += `
        <rect class="chart-bar" x="${xPos}" y="${yPos}" width="${barWidth}" height="${barHeight}" rx="4">
          <title>${escapeHtml(d.label)}: ${d.value}</title>
        </rect>
        <text class="chart-axis-text" x="${xPos + barWidth / 2}" y="${height - 12}" text-anchor="middle">
          ${escapeHtml(d.label)}
        </text>
      `;
    });

    const svg = `
      <svg class="svg-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(title)}">
        <desc>${escapeHtml(title)} Bar Chart</desc>
        ${axesSvg}
        ${barsSvg}
      </svg>
    `;

    container.innerHTML = svg;
  }

  /**
   * Render an accessible SVG Line Chart
   * @param {Object} options
   * @param {HTMLElement} options.container
   * @param {Array<{ label: string, value: number }>} options.data
   * @param {string} options.title
   * @param {number} [options.height]
   */
  static renderLineChart({ container, data = [], title = 'Trend', height = 200 }) {
    if (!container || data.length === 0) return;
    container.innerHTML = '';

    const width = 500;
    const padding = { top: 20, right: 20, bottom: 35, left: 45 };
    const chartWidth = width - padding.left - padding.right;
    const chartHeight = height - padding.top - padding.bottom;

    const maxValue = Math.max(...data.map(d => d.value), 10);
    const stepX = chartWidth / (data.length - 1 || 1);

    const points = data.map((d, i) => {
      const x = padding.left + i * stepX;
      const y = padding.top + chartHeight - (d.value / maxValue) * chartHeight;
      return { x, y, ...d };
    });

    const pathD = points.reduce((acc, p, idx) => {
      return idx === 0 ? `M ${p.x} ${p.y}` : `${acc} L ${p.x} ${p.y}`;
    }, '');

    // Area fill
    const areaD = `${pathD} L ${points[points.length - 1].x} ${padding.top + chartHeight} L ${points[0].x} ${padding.top + chartHeight} Z`;

    let dotsSvg = '';
    let xLabels = '';

    points.forEach(p => {
      dotsSvg += `<circle cx="${p.x}" cy="${p.y}" r="4" fill="var(--brand-primary)" stroke="var(--bg-surface)" stroke-width="2"><title>${escapeHtml(p.label)}: ${p.value}</title></circle>`;
      xLabels += `<text class="chart-axis-text" x="${p.x}" y="${height - 10}" text-anchor="middle">${escapeHtml(p.label)}</text>`;
    });

    const svg = `
      <svg class="svg-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(title)}">
        <defs>
          <linearGradient id="lineGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="var(--brand-primary)" stop-opacity="0.25"/>
            <stop offset="100%" stop-color="var(--brand-primary)" stop-opacity="0.0"/>
          </linearGradient>
        </defs>
        <path d="${areaD}" fill="url(#lineGrad)" />
        <path class="chart-line" d="${pathD}" />
        ${dotsSvg}
        ${xLabels}
      </svg>
    `;

    container.innerHTML = svg;
  }

  /**
   * Render an accessible SVG Donut Gauge
   * @param {Object} options
   * @param {HTMLElement} options.container
   * @param {number} options.percentage - 0 to 100
   * @param {string} options.label
   * @param {string} [options.sublabel]
   */
  static renderDonutGauge({ container, percentage = 0, label = '', sublabel = '' }) {
    if (!container) return;
    container.innerHTML = '';

    const size = 160;
    const strokeWidth = 14;
    const radius = (size - strokeWidth) / 2;
    const circumference = 2 * Math.PI * radius;
    const clampedPct = Math.min(Math.max(percentage, 0), 100);
    const strokeDashoffset = circumference - (clampedPct / 100) * circumference;

    const svg = `
      <svg class="svg-chart" viewBox="0 0 ${size} ${size}" style="max-width: ${size}px; margin: 0 auto;" role="img" aria-label="${clampedPct}% ${escapeHtml(label)}">
        <circle cx="${size / 2}" cy="${size / 2}" r="${radius}" fill="none" stroke="var(--bg-subtle)" stroke-width="${strokeWidth}" />
        <circle cx="${size / 2}" cy="${size / 2}" r="${radius}" fill="none" stroke="var(--brand-primary)" stroke-width="${strokeWidth}"
          stroke-dasharray="${circumference}" stroke-dashoffset="${strokeDashoffset}" stroke-linecap="round"
          transform="rotate(-90 ${size / 2} ${size / 2})" style="transition: stroke-dashoffset 0.6s ease;" />
        <text x="50%" y="48%" text-anchor="middle" dominant-baseline="middle" font-size="24" font-weight="bold" fill="var(--text-primary)">
          ${clampedPct}%
        </text>
        ${sublabel ? `<text x="50%" y="65%" text-anchor="middle" dominant-baseline="middle" font-size="11" fill="var(--text-muted)">${escapeHtml(sublabel)}</text>` : ''}
      </svg>
    `;

    container.innerHTML = svg;
  }
}
