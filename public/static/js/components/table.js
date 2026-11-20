/**
 * Omnea Accessible Data Table Component (table.js)
 * Supports client-side sorting, text search filtering, pagination,
 * CSV export, loading skeletons, and strict XSS escaping.
 */

import { escapeHtml } from '../state.js';

export class DataTable {
  constructor(options = {}) {
    this.container = options.container;
    this.columns = options.columns || []; // [{ key, label, sortable, render }]
    this.data = options.data || [];
    this.pageSize = options.pageSize || 10;
    this.currentPage = 1;
    this.sortKey = options.initialSortKey || null;
    this.sortDirection = options.initialSortDirection || 'asc';
    this.searchQuery = '';
    this.title = options.title || '';
    this.emptyMessage = options.emptyMessage || 'No records found.';
    this.exportFilename = options.exportFilename || 'omnea-export.csv';
    this.tbodyId = options.tbodyId || null;

    this._filteredData = [...this.data];
    this._render();
  }

  setData(newData) {
    this.data = Array.isArray(newData) ? [...newData] : [];
    this._applyFilterAndSort();
    this.currentPage = 1;
    this._renderTableBody();
    this._renderPagination();
  }

  _applyFilterAndSort() {
    let filtered = [...this.data];

    // Search query filter
    if (this.searchQuery) {
      const q = this.searchQuery.toLowerCase();
      filtered = filtered.filter(row => {
        return this.columns.some(col => {
          const val = row[col.key];
          return val !== null && val !== undefined && String(val).toLowerCase().includes(q);
        });
      });
    }

    // Sort
    if (this.sortKey) {
      filtered.sort((a, b) => {
        let valA = a[this.sortKey];
        let valB = b[this.sortKey];

        if (typeof valA === 'string') valA = valA.toLowerCase();
        if (typeof valB === 'string') valB = valB.toLowerCase();

        if (valA < valB) return this.sortDirection === 'asc' ? -1 : 1;
        if (valA > valB) return this.sortDirection === 'asc' ? 1 : -1;
        return 0;
      });
    }

    this._filteredData = filtered;
  }

  _render() {
    if (!this.container) return;
    this.container.innerHTML = '';

    const card = document.createElement('div');
    card.className = 'table-card';

    // Toolbar
    const toolbar = document.createElement('div');
    toolbar.className = 'table-toolbar';

    const searchBox = document.createElement('div');
    searchBox.className = 'table-search-box';
    searchBox.innerHTML = `
      <svg class="table-search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="11" cy="11" r="8"></circle>
        <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
      </svg>
      <input type="text" class="form-input" placeholder="Search table records..." aria-label="Search records">
    `;
    const searchInput = searchBox.querySelector('input');
    searchInput.addEventListener('input', (e) => {
      this.searchQuery = e.target.value.trim();
      this.currentPage = 1;
      this._applyFilterAndSort();
      this._renderTableBody();
      this._renderPagination();
    });

    const actions = document.createElement('div');
    actions.className = 'table-actions';

    const exportBtn = document.createElement('button');
    exportBtn.type = 'button';
    exportBtn.className = 'btn btn-secondary btn-sm btn-export-csv';
    exportBtn.innerHTML = `
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
        <polyline points="7 10 12 15 17 10"></polyline>
        <line x1="12" y1="15" x2="12" y2="3"></line>
      </svg>
      Export CSV
    `;
    exportBtn.addEventListener('click', () => this.exportCsv());

    actions.appendChild(exportBtn);
    toolbar.appendChild(searchBox);
    toolbar.appendChild(actions);

    // Responsive Table Container
    const tableWrap = document.createElement('div');
    tableWrap.className = 'table-responsive';

    const table = document.createElement('table');
    table.className = 'data-table';

    // Header
    const thead = document.createElement('thead');
    const headerRow = document.createElement('tr');

    this.columns.forEach(col => {
      const th = document.createElement('th');
      th.setAttribute('scope', 'col');

      if (col.sortable) {
        th.className = 'sortable-th';
        th.dataset.key = col.key;
        th.innerHTML = `${escapeHtml(col.label)} <span class="sort-indicator">↕</span>`;
        th.addEventListener('click', () => {
          if (this.sortKey === col.key) {
            this.sortDirection = this.sortDirection === 'asc' ? 'desc' : 'asc';
          } else {
            this.sortKey = col.key;
            this.sortDirection = 'asc';
          }
          this._applyFilterAndSort();
          this._updateHeaderSortIndicators(thead);
          this._renderTableBody();
        });
      } else {
        th.textContent = col.label;
      }

      headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    // Body
    const tbody = document.createElement('tbody');
    if (this.tbodyId) {
      tbody.id = this.tbodyId;
    }
    this.tbody = tbody;
    table.appendChild(tbody);
    tableWrap.appendChild(table);

    // Pagination Container
    const pagination = document.createElement('div');
    pagination.className = 'table-pagination';
    this.paginationEl = pagination;

    card.appendChild(toolbar);
    card.appendChild(tableWrap);
    card.appendChild(pagination);
    this.container.appendChild(card);

    this._applyFilterAndSort();
    this._renderTableBody();
    this._renderPagination();
  }

  _updateHeaderSortIndicators(thead) {
    const headers = thead.querySelectorAll('.sortable-th');
    headers.forEach(th => {
      const key = th.dataset.key;
      const indicator = th.querySelector('.sort-indicator');
      if (key === this.sortKey) {
        th.setAttribute('data-sort', this.sortDirection);
        indicator.textContent = this.sortDirection === 'asc' ? '▲' : '▼';
      } else {
        th.removeAttribute('data-sort');
        indicator.textContent = '↕';
      }
    });
  }

  _renderTableBody() {
    if (!this.tbody) return;
    this.tbody.innerHTML = '';

    if (this._filteredData.length === 0) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = this.columns.length;
      td.style.textAlign = 'center';
      td.style.padding = 'var(--space-8)';
      td.innerHTML = `<span style="color: var(--text-muted);">${escapeHtml(this.emptyMessage)}</span>`;
      tr.appendChild(td);
      this.tbody.appendChild(tr);
      return;
    }

    const startIndex = (this.currentPage - 1) * this.pageSize;
    const pageRows = this._filteredData.slice(startIndex, startIndex + this.pageSize);

    pageRows.forEach(row => {
      const tr = document.createElement('tr');
      this.columns.forEach(col => {
        const td = document.createElement('td');
        const rawValue = row[col.key];

        if (typeof col.render === 'function') {
          let rendered;
          try {
            if (rawValue === undefined) {
              rendered = col.render(row, row);
            } else {
              rendered = col.render(rawValue, row);
            }
          } catch (err) {
            try {
              rendered = col.render(row, rawValue);
            } catch (err2) {
              rendered = '—';
            }
          }

          if (rendered instanceof HTMLElement) {
            td.appendChild(rendered);
          } else {
            // Treat as safe string
            td.innerHTML = String(rendered !== undefined && rendered !== null ? rendered : '');
          }
        } else {
          // Strict textContent to prevent XSS
          td.textContent = rawValue !== null && rawValue !== undefined ? String(rawValue) : '—';
        }

        tr.appendChild(td);
      });
      this.tbody.appendChild(tr);
    });
  }

  _renderPagination() {
    if (!this.paginationEl) return;
    this.paginationEl.innerHTML = '';

    const total = this._filteredData.length;
    const totalPages = Math.max(1, Math.ceil(total / this.pageSize));
    const startRecord = total === 0 ? 0 : (this.currentPage - 1) * this.pageSize + 1;
    const endRecord = Math.min(this.currentPage * this.pageSize, total);

    const info = document.createElement('div');
    info.textContent = `Showing ${startRecord} to ${endRecord} of ${total} entries`;

    const controls = document.createElement('div');
    controls.className = 'pagination-controls';

    const prevBtn = document.createElement('button');
    prevBtn.type = 'button';
    prevBtn.className = 'btn btn-secondary btn-sm';
    prevBtn.textContent = 'Previous';
    prevBtn.disabled = this.currentPage <= 1;
    prevBtn.addEventListener('click', () => {
      if (this.currentPage > 1) {
        this.currentPage--;
        this._renderTableBody();
        this._renderPagination();
      }
    });

    const pageIndicator = document.createElement('span');
    pageIndicator.style.margin = '0 var(--space-2)';
    pageIndicator.textContent = `Page ${this.currentPage} of ${totalPages}`;

    const nextBtn = document.createElement('button');
    nextBtn.type = 'button';
    nextBtn.className = 'btn btn-secondary btn-sm';
    nextBtn.textContent = 'Next';
    nextBtn.disabled = this.currentPage >= totalPages;
    nextBtn.addEventListener('click', () => {
      if (this.currentPage < totalPages) {
        this.currentPage++;
        this._renderTableBody();
        this._renderPagination();
      }
    });

    controls.appendChild(prevBtn);
    controls.appendChild(pageIndicator);
    controls.appendChild(nextBtn);

    this.paginationEl.appendChild(info);
    this.paginationEl.appendChild(controls);
  }

  exportCsv() {
    if (this._filteredData.length === 0) return;

    const headers = this.columns.map(c => `"${c.label.replace(/"/g, '""')}"`).join(',');
    const rows = this._filteredData.map(row => {
      return this.columns.map(c => {
        const val = row[c.key];
        let str = val !== null && val !== undefined ? String(val) : '';
        // Mitigate CSV formula injection (prevent execution of =, +, -, @ formulas)
        if (/^[=+\-@]/.test(str)) {
          str = "'" + str;
        }
        return `"${str.replace(/"/g, '""')}"`;
      }).join(',');
    });

    const csvContent = 'data:text/csv;charset=utf-8,' + [headers, ...rows].join('\n');
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement('a');
    link.setAttribute('href', encodedUri);
    link.setAttribute('download', this.exportFilename);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }
}

/**
 * Functional helper to render a DataTable into a newly created container element.
 */
export function renderTable(options = {}) {
  const container = document.createElement('div');
  new DataTable({ container, ...options });
  return container;
}

