// Client API for the Facts backend

export interface FactFreshness {
  age_days: number | null;
  stale: boolean;
  threshold_days: number;
}

export interface Fact {
  id: number;
  text: string;
  source: 'session' | 'facts_db' | 'context_layer';
  verified_at: string | null;
  freshness: FactFreshness;
}

export interface FactsResponse {
  facts: Fact[];
}

class FactsApi {
  private baseUrl = '/api/facts';

  async getFacts(): Promise<FactsResponse> {
    const response = await fetch(this.baseUrl, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(
        errorData.reason || errorData.error || `HTTP ${response.status}: Failed to fetch facts`
      );
    }

    return response.json();
  }

  async createFact(text: string, category?: string): Promise<{ id: number; text: string; source: string; verified_at: string }> {
    const response = await fetch(this.baseUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(category ? { text, category } : { text }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(
        errorData.reason || errorData.error || `HTTP ${response.status}: Failed to create fact`
      );
    }

    return response.json();
  }

  async editFact(id: number, text: string): Promise<{ id: number; text: string; verified_at: string }> {
    const response = await fetch(`${this.baseUrl}/${id}`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ text }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(
        errorData.reason || errorData.error || `HTTP ${response.status}: Failed to edit fact`
      );
    }

    return response.json();
  }

  async markFresh(id: number): Promise<{ id: number; verified_at: string }> {
    const response = await fetch(`${this.baseUrl}/${id}/fresh`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(
        errorData.reason || errorData.error || `HTTP ${response.status}: Failed to mark fact fresh`
      );
    }

    return response.json();
  }
}

export const factsApi = new FactsApi();
