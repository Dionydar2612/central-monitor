-- cm-agent.lua : drop INFO, format records for central  {cm_web, source, file, level, message}
-- message = "<UTC time> [LEVEL] ..." (central reads the level from [LEVEL] after the time, like Linux logs)
local WEB = "__CM_WEB__"

local function days_from_civil(y, m, d)
  y = (m <= 2) and (y - 1) or y
  local era = math.floor(y / 400)
  local yoe = y - era * 400
  local mp = (m + 9) % 12
  local doy = math.floor((153 * mp + 2) / 5) + d - 1
  local doe = yoe * 365 + math.floor(yoe / 4) - math.floor(yoe / 100) + doy
  return era * 146097 + doe - 719468
end

local function utc_epoch(y, mo, d, h, mi, s)
  return days_from_civil(tonumber(y), tonumber(mo), tonumber(d)) * 86400 + tonumber(h) * 3600 + tonumber(mi) * 60 + tonumber(s)
end

local function iso(sec) return os.date("!%Y-%m-%dT%H:%M:%SZ", math.floor(sec)) end
local function oneline(s) return (tostring(s or ""):gsub("[\r\n\t]+", " ")) end
local function basename(p) return (tostring(p or ""):match("([^\\/]+)$") or "") end

local WORDS = { error="ERROR", err="ERROR", critical="ERROR", crit="ERROR", fatal="ERROR", alert="ERROR",
  emerg="ERROR", emergency="ERROR", severe="ERROR", panic="ERROR", warn="WARN", warning="WARN",
  info="INFO", information="INFO", notice="INFO", debug="INFO", trace="INFO", verbose="INFO" }

-- level written by the log itself: level=xxx / severity: xxx / [XXX] / an upper-case word near the start
local function explicit_level(line)
  local low = line:lower()
  local w = low:match("level[\"']?%s*[=:]%s*[\"']?(%a+)") or low:match("severity[\"']?%s*[=:]%s*[\"']?(%a+)")
         or low:match("%[%s*(%a+)%s*%]")
  if w and WORDS[w] then return WORDS[w] end
  for word in line:sub(1, 160):gmatch("%f[%w]%u%u%u+%f[%W]") do
    if WORDS[word:lower()] then return WORDS[word:lower()] end
  end
  return nil
end

local BENIGN = { "no errors?", "0 errors?", "zero errors?", "without errors?", "no failures?", "0 failures?",
  "errors?%s*[=:]%s*0", "failures?%s*[=:]%s*0", "failed%s*[=:]%s*0", "error[_%.%-]?log", "error[_%.%-]?page" }
local ERRORS = { "%f[%a]error", "%f[%a]exception", "%f[%a]fail", "%f[%a]fatal", "%f[%a]critical", "%f[%a]panic",
  "traceback", "segfault", "refused", "timed out", "denied", "unreachable", "out of memory", "no space left" }

local function guess_level(line)
  local lv = explicit_level(line)
  if lv then return lv end
  local low = line:lower()
  for _, p in ipairs(BENIGN) do low = low:gsub(p, " ") end
  for _, p in ipairs(ERRORS) do if low:find(p) then return "ERROR" end end
  if low:find("%f[%a]warn") or low:find("deprecated") then return "WARN" end
  return "INFO"
end

-- Event Log: Level 1 Critical, 2 Error, 3 Warning (4 Information, 0 LogAlways, 5 Verbose = dropped)
function cm_event(tag, ts, r)
  local lv = tonumber(r["Level"])
  if not lv then
    local s = tostring(r["Level"] or ""):lower()
    lv = (s == "critical" and 1) or (s == "error" and 2) or (s:find("warn") and 3) or 4
  end
  if lv < 1 or lv > 3 then return -1, 0, 0 end
  local level = (lv <= 2) and "ERROR" or "WARN"

  local t = ts
  local y, mo, d, h, mi, s, sign, oh, om = tostring(r["TimeCreated"] or ""):match(
      "(%d+)-(%d+)-(%d+)[ T](%d+):(%d+):(%d+)[%.%d]*%s*([%+%-])(%d%d):?(%d%d)")
  if y then
    local off = (tonumber(oh) * 3600 + tonumber(om) * 60) * ((sign == "-") and -1 or 1)
    t = utc_epoch(y, mo, d, h, mi, s) - off
  end

  local chan = tostring(r["Channel"] or "Event")
  local msg = string.format("%s [%s] [%s] %s (Event %s): %s", iso(t), level, chan,
      tostring(r["ProviderName"] or "-"), tostring(r["EventID"] or "-"), oneline(r["Message"]))
  return 1, t, { cm_web = WEB, source = "eventlog", file = chan, level = level, message = msg }
end

-- plain log files + IIS (W3C: time is UTC, ERROR when the HTTP status is 5xx)
function cm_file(tag, ts, r)
  local line = oneline(r["log"])
  if line == "" or line:sub(1, 1) == "#" then return -1, 0, 0 end
  local path = tostring(r["cm_path"] or "")
  local file = basename(path)

  local y, mo, d, h, mi, s, rest = line:match("^(%d%d%d%d)%-(%d%d)%-(%d%d) (%d%d):(%d%d):(%d%d) (.*)$")
  if y and path:lower():find("inetpub") then
    local f = {}
    for x in rest:gmatch("%S+") do f[#f + 1] = x end
    local status = tonumber(f[#f - 3] or "")
    if not status or status < 500 then return -1, 0, 0 end
    local t = utc_epoch(y, mo, d, h, mi, s)
    return 1, t, { cm_web = WEB, source = "iis", file = file, level = "ERROR",
                   message = string.format("%s [ERROR] HTTP %d %s", iso(t), status, rest) }
  end

  local level = guess_level(line)
  if level == "INFO" then return -1, 0, 0 end
  return 2, ts, { cm_web = WEB, source = "application", file = file, level = level,
                  message = string.format("%s [%s] %s", iso(ts), level, line) }
end
