import os, time
from . import api, store, handlers, dispatch

RUN_SECONDS = int(os.getenv("RUN_SECONDS", "240"))

def main():
    api.delete_webhook()
    api.set_commands()
    st = store.load_state()
    db = store.load_users()
    offset = int(st.get("offset") or 0)
    deadline = time.time() + RUN_SECONDS
    handled = 0
    while time.time() < deadline:
        wait = int(max(1, min(25, deadline - time.time())))
        try:
            ups = api.get_updates(offset, timeout=wait)
        except Exception as e:
            print("getUpdates: " + str(e)[:150])
            time.sleep(3)
            continue
        for up in ups:
            offset = up["update_id"] + 1
            try:
                if "message" in up:
                    handlers.handle_message(db, up["message"])
                elif "callback_query" in up:
                    handlers.handle_callback(db, up["callback_query"])
                handled += 1
            except Exception as e:
                print("handler error: " + str(e)[:150])
        if ups:
            store.save_users(db)
            store.save_state({"offset": offset})
    store.save_users(db)
    store.save_state({"offset": offset})
    print("polled, handled=" + str(handled))
    try:
        dispatch.dispatch("instant")
    except Exception as e:
        print("dispatch failed: " + str(e)[:150])

if __name__ == "__main__":
    main()
