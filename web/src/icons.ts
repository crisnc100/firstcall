// Lucide icons (ISC licence), drawn from their path data as SVG elements.

type Shape = [string, Record<string, string>];

const P = (d: string): Shape => ["path", { d }];
const ICONS: Record<string, Shape[]> = {
  clock: [["circle", { cx: "12", cy: "12", r: "10" }], P("M12 6v6l4 2")],
  triangle: [
    P("m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"),
    P("M12 9v4"),
    P("M12 17h.01"),
  ],
  octagon: [
    P(
      "M2.586 16.726A2 2 0 0 1 2 15.312V8.688a2 2 0 0 1 .586-1.414l4.688-4.688A2 2 0 0 1 8.688 2h6.624a2 2 0 0 1 1.414.586l4.688 4.688A2 2 0 0 1 22 8.688v6.624a2 2 0 0 1-.586 1.414l-4.688 4.688a2 2 0 0 1-1.414.586H8.688a2 2 0 0 1-1.414-.586z",
    ),
    P("M12 8v4"),
    P("M12 16h.01"),
  ],
  lock: [["rect", { width: "18", height: "11", x: "3", y: "11", rx: "2" }], P("M7 11V7a5 5 0 0 1 10 0v4")],
  loader: [P("M21 12a9 9 0 1 1-6.219-8.56")],
  check: [P("M20 6 9 17l-5-5")],
  inbox: [
    P("M22 12h-6l-2 3h-4l-2-3H2"),
    P(
      "M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z",
    ),
  ],
  users: [
    P("M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"),
    ["circle", { cx: "9", cy: "7", r: "4" }],
    P("M22 21v-2a4 4 0 0 0-3-3.87"),
    P("M16 3.13a4 4 0 0 1 0 7.75"),
  ],
  user: [P("M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"), ["circle", { cx: "12", cy: "7", r: "4" }]],
  list: [P("M3 12h.01"), P("M3 18h.01"), P("M3 6h.01"), P("M8 12h13"), P("M8 18h13"), P("M8 6h13")],
  bell: [
    P("M10.268 21a2 2 0 0 0 3.464 0"),
    P(
      "M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326",
    ),
  ],
  copy: [
    ["rect", { width: "14", height: "14", x: "8", y: "8", rx: "2", ry: "2" }],
    P("M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"),
  ],
  info: [["circle", { cx: "12", cy: "12", r: "10" }], P("M12 16v-4"), P("M12 8h.01")],
  alert: [["circle", { cx: "12", cy: "12", r: "10" }], P("M12 8v4"), P("M12 16h.01")],
  more: [
    ["circle", { cx: "12", cy: "12", r: "1" }],
    ["circle", { cx: "19", cy: "12", r: "1" }],
    ["circle", { cx: "5", cy: "12", r: "1" }],
  ],
  tray: [P("M3 7h18"), P("M5 7v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7"), P("M9 3h6l1 4H8z")],
  hourglass: [P("M5 22h14"), P("M5 2h14"), P("M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22"), P("M7 2v4.172a2 2 0 0 0 .586 1.414L12 12l4.414-4.414A2 2 0 0 0 17 6.172V2")],
  pin: [P("M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0"), ["circle", { cx: "12", cy: "10", r: "3" }]],
};

export type IconName = keyof typeof ICONS;

export function icon(name: IconName, large = false, extra = ""): SVGSVGElement {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", `icon${large ? " lg" : ""}${extra ? ` ${extra}` : ""}`);
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  for (const [tag, attrs] of ICONS[name]) {
    const shape = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs)) shape.setAttribute(key, value);
    svg.appendChild(shape);
  }
  return svg;
}
