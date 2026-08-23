package.path = table.concat({
    "./?.lua",
    "../?.lua",
    package.path,
}, ";")

-- What a failed sync call tells the user, and where.
--
-- #920: a KOReader delete showed "Server push failed" while the reporter's
-- server log recorded no push at all -- the request never left the device. The
-- error that would have named why was dropped twice over: it went to
-- `logger.dbg`, which KOReader suppresses unless debug logging is on, and the
-- callback was handed `res.body`, which is nil on a raise because `pcall`
-- returns the error rather than a response. So the one failure mode with no
-- server-side trace also had no device-side trace, and three diagnoses were
-- guessed instead of read.
--
-- These pin the contract that closes that: a failed call always reports a
-- reason, and always logs at a level the user actually gets.

local warnings = {}
local debugs = {}

package.preload["ui/uimanager"] = function()
    return { looper = nil, setInputTimeout = function() end }
end
package.preload["logger"] = function()
    return {
        warn = function(...) table.insert(warnings, { ... }) end,
        dbg = function(...) table.insert(debugs, { ... }) end,
        info = function() end,
        err = function() end,
    }
end
package.preload["socketutil"] = function()
    return { set_timeout = function() end, reset_timeout = function() end }
end

local CWASyncClient = require("CWASyncClient")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s", message, tostring(expected), tostring(actual)), 2)
    end
end

local function assertTruthy(value, message)
    if not value then error(message, 2) end
end

local function testDescribeFailureNamesEveryShape()
    local describe = CWASyncClient.describeFailure
    -- lua-Spore raises a table; the transport raises a string. Both used to be
    -- flattened to nil.
    assertEqual(describe({ message = "connection refused" }), "connection refused",
        "table error: message is preferred")
    assertEqual(describe({ error = "bad_request" }), "bad_request",
        "table error: falls back to error")
    assertEqual(describe({ reason = "timeout" }), "timeout",
        "table error: falls back to reason")
    assertEqual(describe({ status = 503 }), "HTTP 503",
        "table error with only a status still names it")
    assertEqual(describe("api.json: missing required parameter"), "api.json: missing required parameter",
        "string error is passed through verbatim")
    -- The catch-all has to stay a sentence, never nil: an empty reason would
    -- put us straight back to a bare "Server push failed".
    assertEqual(describe(nil), "no response from server", "nil error still names something")
    assertEqual(describe(""), "no response from server", "empty error still names something")
    assertEqual(describe({}), "no response from server", "featureless table still names something")
end

local function testRaisedCallReportsAReasonAndWarns()
    warnings, debugs = {}, {}
    local seen
    CWASyncClient._reportOutcome(function(ok, body, reason)
        seen = { ok = ok, body = body, reason = reason }
    end, false, "attempt to index a nil value", "CWASyncClient:push_annotations")

    assertEqual(seen.ok, false, "a raise is not a success")
    -- The regression: this used to be nil, so the caller could only say
    -- "Server push failed" with nothing after the colon.
    assertEqual(seen.reason, "attempt to index a nil value", "the raise reason reaches the caller")
    assertEqual(#warnings, 1, "a failed call logs exactly once, at warn")
    assertEqual(#debugs, 0, "nothing is written at dbg, which users do not have on")
    assertTruthy(tostring(warnings[1][1]):find("push_annotations", 1, true),
        "the warning names which call failed")
end

local function testNon200ReportsItsStatus()
    warnings, debugs = {}, {}
    local seen
    CWASyncClient._reportOutcome(function(ok, body, reason)
        seen = { ok = ok, body = body, reason = reason }
    end, true, { status = 400, body = { error = "invalid_deleted" } }, "CWASyncClient:push_annotations")

    assertEqual(seen.ok, false, "400 is not a success")
    assertEqual(seen.reason, "HTTP 400", "a rejection reports its status")
    -- A call that reached the server keeps its body: the server names the field
    -- it objected to (#1101) and the caller may want it.
    assertEqual(seen.body.error, "invalid_deleted", "the server's own error survives")
    assertEqual(#warnings, 0, "a completed call is not a client-side fault")
end

local function testSuccessCarriesNoReason()
    warnings, debugs = {}, {}
    local seen
    CWASyncClient._reportOutcome(function(ok, body, reason)
        seen = { ok = ok, body = body, reason = reason }
    end, true, { status = 200, body = { created = 1 } }, "CWASyncClient:push_annotations")

    assertEqual(seen.ok, true, "200 is a success")
    assertEqual(seen.reason, nil, "a success has no reason, so callers can branch on it")
    assertEqual(seen.body.created, 1, "the response body is passed through")
    assertEqual(#warnings, 0, "a success logs nothing")
end

-- The server keeps every rejection funnelled through one `_reject` so the log
-- cannot regress to silence one branch at a time (#1101). The device half needs
-- the same guard: a new sync call that hand-rolls `logger.dbg` would be
-- invisible again, and only on that one path, which is the hardest kind of gap
-- to notice.
local function testNoSyncFailureIsWrittenAtDbg()
    local source = io.open("../CWASyncClient.lua", "r")
    assertTruthy(source, "CWASyncClient.lua is readable from the tests directory")
    local text = source:read("*a")
    source:close()

    -- The call form, not the bare name: the comment above `describeFailure`
    -- explains what dbg used to cost us and must stay quotable.
    assertEqual(text:find("logger.dbg(", 1, true), nil,
        "no failure path may log at dbg -- KOReader suppresses it unless the user "
        .. "enabled debug logging, which is how #920 lost its only device-side trace")
    assertTruthy(text:find("logger.warn(", 1, true), "failures are logged at warn")
end

local function fakePushClient(responses)
    local calls = {}
    local transport = {
        reset_middlewares = function() end,
        enable = function() end,
        push_annotations = function(_, payload)
            calls[#calls + 1] = payload
            local response = responses[#calls]
            if type(response) == "function" then return response() end
            return response or { status = 200, body = { deleted = #payload.deleted } }
        end,
    }
    return { client = transport }, calls
end

local function deletedIds(count)
    local deleted = {}
    for i = 1, count do deleted[i] = "annotation-" .. tostring(i) end
    return deleted
end

local function testDeletePushPreservesZeroAndBoundaryRequestShapes()
    local annotations = { { annotation_id = "live-1" } }

    local subject, calls = fakePushClient({
        { status = 200, body = { updated = 1 } },
    })
    local outcomes = {}
    CWASyncClient.push_annotations(subject, "user", "pass", "digest",
        annotations, {}, function(ok)
            outcomes[#outcomes + 1] = ok
        end)
    assertEqual(#calls, 1, "zero deletes preserve the original one-request push")
    assertEqual(calls[1].annotations, annotations,
        "a no-delete push still carries every live annotation")
    assertEqual(calls[1].deleted, nil,
        "a no-delete push does not invent delete authority")
    assertEqual(#outcomes, 1, "a no-delete push completes exactly once")
    assertEqual(outcomes[1], true, "a successful no-delete push remains successful")

    subject, calls = fakePushClient({})
    CWASyncClient.push_annotations(subject, "user", "pass", "digest",
        annotations, deletedIds(200), function() end)
    assertEqual(#calls, 1, "exactly 200 delete ids remain one request")
    assertEqual(#calls[1].deleted, 200, "the boundary request carries all 200 ids")
    assertEqual(calls[1].annotations, annotations,
        "the boundary request carries the live set")

    subject, calls = fakePushClient({})
    CWASyncClient.push_annotations(subject, "user", "pass", "digest",
        annotations, deletedIds(201), function() end)
    assertEqual(#calls, 2, "201 delete ids cross the boundary exactly once")
    assertEqual(#calls[1].deleted, 200, "the first boundary chunk is capped")
    assertEqual(#calls[2].deleted, 1, "the second boundary chunk has the remainder")
    assertEqual(calls[1].annotations, annotations,
        "the live set rides the first boundary chunk")
    assertEqual(#calls[2].annotations, 0,
        "the boundary continuation is delete-only")
end

-- F-e4da4d: delete request size is bounded on the slow device side, while the
-- logical push remains one all-or-failed operation to the caller. The caller
-- advances its watermark only when this callback says every chunk succeeded.
local function testDeletePushIsBoundedAndCompletesOnce()
    local deleted = deletedIds(451)
    local annotations = { { annotation_id = "live-1" } }
    local subject, calls = fakePushClient({})
    local outcomes = {}

    CWASyncClient.push_annotations(subject, "user", "pass", "digest",
        annotations, deleted, function(ok, body, reason)
            outcomes[#outcomes + 1] = { ok = ok, body = body, reason = reason }
        end)

    assertEqual(#calls, 3, "451 delete ids are sent as three bounded requests")
    assertEqual(#calls[1].deleted, 200, "the first delete chunk is capped at 200 ids")
    assertEqual(#calls[2].deleted, 200, "the second delete chunk is capped at 200 ids")
    assertEqual(#calls[3].deleted, 51, "the final delete chunk carries the remainder")
    assertEqual(calls[1].deleted[1], "annotation-1", "chunking preserves first-id order")
    assertEqual(calls[3].deleted[51], "annotation-451", "chunking preserves last-id order")
    assertEqual(calls[1].annotations, annotations,
        "live annotations ride the first request exactly once")
    assertEqual(#calls[2].annotations, 0,
        "later chunks do not replay the complete annotation set")
    assertEqual(#calls[3].annotations, 0,
        "the final chunk contains deletes only")
    assertEqual(calls[1].delete_source, "koreader", "every delete chunk names its authority")
    assertEqual(calls[2].delete_source, "koreader", "later chunks keep delete authority")
    assertEqual(#outcomes, 1, "the logical push completes exactly once")
    assertEqual(outcomes[1].ok, true, "all successful chunks report one success")
    assertEqual(outcomes[1].reason, nil, "the combined success has no failure reason")
end

local function testDeletePushFailureStopsAndCannotCompleteWatermark()
    local deleted = deletedIds(401)
    local subject, calls = fakePushClient({
        { status = 200, body = { deleted = 200 } },
        { status = 503, body = { error = "busy" } },
        { status = 200, body = { deleted = 1 } },
    })
    local outcomes = {}
    local watermark_saved = false

    CWASyncClient.push_annotations(subject, "user", "pass", "digest", {}, deleted,
        function(ok, body, reason)
            outcomes[#outcomes + 1] = { ok = ok, body = body, reason = reason }
            -- This is the exact caller contract main.lua uses: it saves only
            -- inside `if ok2 and plan.may_save_watermark then`.
            if ok then watermark_saved = true end
        end)

    assertEqual(#calls, 2, "a failed second chunk prevents the third request")
    assertEqual(#outcomes, 1, "a partial server delete still completes the callback once")
    assertEqual(outcomes[1].ok, false,
        "a partial delete is never reported as complete to the watermark caller")
    assertEqual(outcomes[1].reason, "HTTP 503", "the failed chunk status reaches the user")
    assertEqual(outcomes[1].body.error, "busy", "the failed chunk body is preserved")
    assertEqual(watermark_saved, false,
        "a partial delete cannot make the caller treat its watermark as complete")
end

testDescribeFailureNamesEveryShape()
testNoSyncFailureIsWrittenAtDbg()
testRaisedCallReportsAReasonAndWarns()
testNon200ReportsItsStatus()
testSuccessCarriesNoReason()
testDeletePushPreservesZeroAndBoundaryRequestShapes()
testDeletePushIsBoundedAndCompletesOnce()
testDeletePushFailureStopsAndCannotCompleteWatermark()

print("sync_client outcome-reporting tests passed")
