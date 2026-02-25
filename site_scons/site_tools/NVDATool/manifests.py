import codecs



def generateManifest(source: str, dest: str, addon_info: dict):
	with codecs.open(source, "r", "utf-8") as f:
		manifest_template = f.read()
	manifest = manifest_template.format(**addon_info)
	with codecs.open(dest, "w", "utf-8") as f:
		f.write(manifest)
