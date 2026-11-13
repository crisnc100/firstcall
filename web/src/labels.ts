// The words the pages use for a message's source, category and status, so no page shows
// a raw slug or state name.

const SOURCES: Record<string, string> = {
  inbox: "Email",
  "site-form": "Web form",
  "partner-form": "Partner form",
  calls: "Phone call",
};

const CATEGORIES: Record<string, string> = {
  lead: "Lead",
  vendor: "Vendor",
  support: "Support",
  spam: "Spam",
};

const STATUSES: Record<string, string> = {
  received: "Waiting to be read",
  processing: "Being read",
  lead_created: "First message",
  attached: "Follow-up",
  not_a_lead: "Not a lead",
  duplicate_content: "Repeat",
  failed: "Couldn't be read",
};

function plain(value: string): string {
  const words = value.replace(/[-_]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function sourceLabel(slug: string): string {
  return SOURCES[slug] ?? plain(slug);
}

export function categoryLabel(category: string | null | undefined): string {
  return category ? (CATEGORIES[category] ?? plain(category)) : "Other";
}

export function statusLabel(status: string): string {
  return STATUSES[status] ?? plain(status);
}
