package.path = table.concat({
    "./?.lua",
    "../?.lua",
    package.path,
}, ";")

-- The provider's digest guard is pure, but loading the production module also
-- loads helpers used only when portable annotations are materialized.  Keep
-- those dependencies inert so this suite executes the real setContext/readAll
-- boundary without requiring a KOReader process.
package.preload["json"] = function()
    return { encode = function(value) return tostring(value) end }
end
package.preload["ffi/sha2"] = function()
    return { md5 = function(value) return "md5-" .. tostring(value) end }
end

local Provider = require("koreader_annotations_provider")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s",
            message, tostring(expected), tostring(actual)), 2)
    end
end

-- The positive half is deliberately first.  A fail-closed predicate that
-- rejects every read is data-safe but unusably strict, and must fail before a
-- stale-context assertion can make the suite look safety-complete.
Provider.setContext({ annotation = { annotations = {
    { text = "current book", datetime = "2026-08-22T00:00:00Z", page = 1 },
} } }, "digest-current")
local current = Provider.readAll(nil, "digest-current")
assertEqual(type(current), "table",
    "a normal single-book sync accepts its own digest")
assertEqual(#current, 1,
    "the accepted current context contributes its real annotation set")

-- Simulate a second sync replacing the module singleton before the first
-- pull's callback reads it.  The older callback must not consume the new book.
Provider.setContext({ annotation = { annotations = {
    { text = "next book", datetime = "2026-08-22T00:00:01Z", page = 2 },
} } }, "digest-next")
assertEqual(Provider.readAll(nil, "digest-current"), nil,
    "a stale callback refuses a different book's live collection")

-- Compatibility: callers which do not bind a digest retain the pre-fix read
-- contract.  The safety path always supplies one from main.lua, but the
-- provider interface remains optional rather than globally stricter.
assertEqual(#Provider.readAll(nil, nil), 1,
    "an omitted expected digest does not reject a readable current context")

Provider.setContext(nil, nil)
print("digest binding safety tests passed")
