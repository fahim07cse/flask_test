/*
  Small Supabase-style compatibility client for GitHub Pages → Flask → Supabase.
  It keeps the existing page logic mostly unchanged while routing all
  browser database operations to the Flask backend; Flask connects privately to Supabase PostgreSQL.
*/
(function () {
  const TOKEN_KEY = 'facultyAiAdminToken';

  function getAdminToken() {
    return sessionStorage.getItem(TOKEN_KEY) || '';
  }

  function setAdminToken(token) {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  }

  function apiHeaders(extra = {}) {
    const headers = { ...extra };
    const token = getAdminToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    return headers;
  }

  // Existing admin HTML only changes the UI on logout. Clear the API token
  // automatically as well, without requiring changes to that page.
  document.addEventListener('click', event => {
    if (event.target && event.target.closest && event.target.closest('#logoutBtn')) {
      setAdminToken('');
    }
  }, true);

  class QueryBuilder {
    constructor(table) {
      this.table = table;
      this.operation = 'select';
      this.columns = '*';
      this.payload = null;
      this.filters = [];
      this.orderBy = null;
      this.limitValue = null;
      this.returning = false;
    }

    select(columns = '*') {
      if (this.operation === 'insert' || this.operation === 'update') {
        this.returning = true;
        this.columns = columns || '*';
      } else {
        this.operation = 'select';
        this.columns = columns || '*';
      }
      return this;
    }

    insert(payload) {
      this.operation = 'insert';
      this.payload = payload;
      return this;
    }

    update(payload) {
      this.operation = 'update';
      this.payload = payload;
      return this;
    }

    eq(column, value) {
      this.filters.push({ type: 'eq', column, value });
      return this;
    }

    ilike(column, value) {
      this.filters.push({ type: 'ilike', column, value });
      return this;
    }

    order(column, options = {}) {
      this.orderBy = { column, ascending: options.ascending !== false };
      return this;
    }

    limit(value) {
      this.limitValue = Number(value);
      return this;
    }

    async execute() {
      try {
        const params = new URLSearchParams();
        if (this.columns) params.set('select', this.columns);
        for (const f of this.filters) {
          params.append(`${f.type}_${f.column}`, String(f.value));
        }
        if (this.orderBy) {
          params.set('order', `${this.orderBy.column}.${this.orderBy.ascending ? 'asc' : 'desc'}`);
        }
        if (Number.isFinite(this.limitValue)) params.set('limit', String(this.limitValue));
        if (this.returning) params.set('returning', 'true');

        const base = String(window.FLASK_API_BASE_URL || '').replace(/\/$/, '');
        const url = `${base}/api/table/${encodeURIComponent(this.table)}?${params.toString()}`;
        let response;
        if (this.operation === 'select') {
          response = await fetch(url, { credentials: 'include', headers: apiHeaders() });
        } else if (this.operation === 'insert') {
          response = await fetch(url, {
            method: 'POST',
            credentials: 'include',
            headers: apiHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(this.payload)
          });
        } else if (this.operation === 'update') {
          response = await fetch(url, {
            method: 'PATCH',
            credentials: 'include',
            headers: apiHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(this.payload)
          });
        }

        const body = await response.json().catch(() => ({}));
        if (!response.ok) {
          return { data: null, error: { message: body.error || `HTTP ${response.status}` } };
        }
        return { data: body.data ?? [], error: null };
      } catch (err) {
        return { data: null, error: { message: err.message || String(err) } };
      }
    }

    then(resolve, reject) {
      return this.execute().then(resolve, reject);
    }
  }

  window.createAzureClient = function createAzureClient() {
    return {
      from(table) {
        return new QueryBuilder(table);
      },
      async rpc(name, args = {}) {
        try {
          const response = await fetch(`${String(window.FLASK_API_BASE_URL || '').replace(/\/$/, '')}/api/rpc/${encodeURIComponent(name)}`, {
            method: 'POST',
            credentials: 'include',
            headers: apiHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(args)
          });
          const body = await response.json().catch(() => ({}));
          if (!response.ok) {
            return { data: null, error: { message: body.error || `HTTP ${response.status}` } };
          }
          if (name === 'verify_admin_login') {
            setAdminToken(body.admin_token || '');
          }
          return { data: body.data ?? [], error: null };
        } catch (err) {
          return { data: null, error: { message: err.message || String(err) } };
        }
      }
    };
  };
})();
