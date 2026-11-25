/** The update note parts that explain an updater error. */
export type UpdateErrorExplanation = {
  title: string;
  hint?: string;
  /** The device's own error text, shown small under the explanation. */
  detail?: string;
};

/** Plain-language explanation of an updater error; the raw text stays visible as detail. */
export function explainUpdateError(raw: string | null): UpdateErrorExplanation {
  const detail = raw ?? undefined;
  const text = (raw ?? "").toLowerCase();
  if (/meteorcli-release\.json(\.sig)?: http 404/.test(text)) {
    return {
      title: "No meteorcli release to install",
      hint:
        "The release server has no Go meteorcli release yet (the newest stable release may be " +
        "older than the Go agent), or the device's release URL points to the wrong place.",
      detail,
    };
  }
  if (/http 40[13]/.test(text)) {
    return {
      title: "The release server refused the download",
      hint: "Check the device's release URL; it must be a public folder of release files.",
      detail,
    };
  }
  if (/http 5\d\d/.test(text)) {
    return {
      title: "The release server had an error",
      hint: "Try again in a few minutes.",
      detail,
    };
  }
  if (/unsigned|signature|not signed/.test(text)) {
    return {
      title: "The release is not signed",
      hint: "The device installs only releases signed with the MeteorCloud release key, so it refused this one.",
      detail,
    };
  }
  if (/roll(ed|ing)? ?back/.test(text)) {
    return {
      title: "The new version did not start; the previous one is back",
      hint: "The device kept running the version it had. Run diagnostics or open its logs to see why.",
      detail,
    };
  }
  if (/no such host|dial tcp|timeout|connection refused|network is unreachable|tls/.test(text)) {
    return {
      title: "The device could not reach the release server",
      hint: "Check the device's internet connection and DNS.",
      detail,
    };
  }
  if (/not enough free space/.test(text)) {
    return { title: "Not enough free space on the device", detail };
  }
  return { title: "The device could not update meteorcli", detail };
}
