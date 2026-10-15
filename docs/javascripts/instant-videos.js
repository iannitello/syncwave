// Instant navigation inserts the next page from a parsed document. A video
// moved in that way never starts loading until load() is called again.
var skipInitialVideos = true
document$.subscribe(function () {
  if (skipInitialVideos) {
    skipInitialVideos = false
    return
  }
  document.querySelectorAll("video").forEach(function (video) {
    var start = function () {
      var pending = video.play()
      if (pending && pending.catch) pending.catch(function () {})
    }
    video.load()
    if (video.readyState >= 2) start()
    else video.addEventListener("loadeddata", start, { once: true })
  })
})
