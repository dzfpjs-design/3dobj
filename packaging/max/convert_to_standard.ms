-- TankExtract: assign one 3ds Max Standard material after an FBX import.
--
-- Blender writes a Phong/Lambert surface. An FBX file does not carry the
-- 3ds Max Standard material class. This script creates that class and
-- assigns it. It has not been run in 3ds Max in this repository, because
-- 3ds Max is not available here.
--
-- In 3ds Max: Scripting > Run Script, and choose this file.
-- Or: MAXScript > Run Script. The Listener prints how many geometry
-- objects were updated.

fn tankExtractAssignStandard = (
    local stdMat = Standardmaterial name:"Standard"
    stdMat.diffuse = color 220 220 220
    stdMat.shaderType = 1
    local count = 0
    for obj in objects where isKindOf obj GeometryClass do (
        obj.material = stdMat
        count += 1
    )
    format "TankExtract: assigned Standard material to % geometry objects. Not verified by an automated test.\n" count
    count
)

tankExtractAssignStandard()
