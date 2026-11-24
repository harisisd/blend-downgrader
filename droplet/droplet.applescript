on run
	set f to choose file with prompt "Pilih file .blend (Blender 4.x/5.x) yang ingin dibuka di Blender 3:" of type {"blend", "org.blender.blend"} with multiple selections allowed
	convertFiles(f)
end run

on open droppedItems
	convertFiles(droppedItems)
end open

on convertFiles(theFiles)
	set scriptPath to POSIX path of (path to resource "blend2v3.sh")
	set args to ""
	repeat with f in theFiles
		set args to args & " " & quoted form of POSIX path of f
	end repeat
	try
		set out to do shell script "/bin/bash " & quoted form of scriptPath & args & " 2>&1"
		display dialog out buttons {"OK"} default button 1 with title "Blend → Blender 3 : Berhasil"
	on error errMsg
		display dialog errMsg buttons {"OK"} default button 1 with icon caution with title "Blend → Blender 3 : Ada yang gagal"
	end try
end convertFiles
