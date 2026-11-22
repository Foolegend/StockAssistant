const MAX_AGE_DAYS = 7;

function extractStockData(output) {
  const marker = /{\s*"analysis_date"\s*:/.exec(output);
  if (!marker) {
    return null;
  }

  const start = marker.index;
  const stack = [];
  let inString = false;
  let escaped = false;
  for (let index = start; index < output.length; index += 1) {
    const character = output[index];
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (character === "\\") {
        escaped = true;
      } else if (character === '"') {
        inString = false;
      }
      continue;
    }

    if (character === '"') {
      inString = true;
    } else if (character === "{" || character === "[") {
      stack.push(character);
    } else if (character === "}" || character === "]") {
      stack.pop();
      if (stack.length === 0) {
        return JSON.parse(output.slice(start, index + 1));
      }
    }
  }

  throw new Error("Stock data output contains incomplete JSON.");
}

function dateValue(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return null;
  }
  const timestamp = Date.parse(`${value}T00:00:00Z`);
  if (!Number.isFinite(timestamp) || new Date(timestamp).toISOString().slice(0, 10) !== value) {
    return null;
  }
  return timestamp;
}

function validateStockData(payload) {
  const output = payload.tool_output && payload.tool_output.output;
  if (typeof output !== "string" || output.trim() === "" || output.trim() === "<no output>") {
    console.log("[Stock data freshness hook] Tool returned no output; skipping validation.");
    return;
  }

  const data = extractStockData(output);
  if (data === null) {
    // The fetcher writes JSON to stdout. No payload here means the command
    // failed, its stdout was redirected to a file, or it printed something
    // else -- there is no data for this hook to judge, so do not block the
    // agent. The agent still sees the tool's own output/exit code.
    console.log(
      "[Stock data freshness hook] Tool output has no stock JSON payload; skipping validation.",
    );
    return;
  }

  if (!Array.isArray(data.stocks) || data.stocks.length === 0) {
    throw new Error("Stock data output contains no successful stock records.");
  }

  const now = new Date();
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  for (const stock of data.stocks) {
    const bars = stock && stock.recent_bars;
    if (!Array.isArray(bars) || bars.length === 0) {
      throw new Error(`Stock ${stock && stock.code ? stock.code : "(unknown)"} has no dated price bars.`);
    }

    const dates = bars.map((bar) => dateValue(bar && bar.date));
    if (dates.some((date) => date === null)) {
      throw new Error(`Stock ${stock.code || "(unknown)"} has a missing or invalid price-bar date.`);
    }

    const latestDate = Math.max(...dates);
    const ageDays = Math.floor((today - latestDate) / 86400000);
    if (ageDays < 0) {
      throw new Error(`Stock ${stock.code || "(unknown)"} has a future price-bar date.`);
    }
    if (ageDays > MAX_AGE_DAYS) {
      const date = new Date(latestDate).toISOString().slice(0, 10);
      throw new Error(
        `Stock ${stock.code || "(unknown)"} latest price bar is ${ageDays} days old (${date}); maximum is ${MAX_AGE_DAYS}.`,
      );
    }
  }
}

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => {
  try {
    validateStockData(JSON.parse(input));
  } catch (error) {
    console.error(`[Stock data freshness hook] ${error.message}`);
    process.exitCode = 1;
  }
});
