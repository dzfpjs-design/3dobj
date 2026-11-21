-- TankExtract: turn imported Phong/Lambert materials into 3ds Max Standard.
--
-- An FBX file does not carry the 3ds Max Standard material class. Blender
-- writes a Phong/Lambert surface, often named Standard.
--
-- Scope: the current selection, when it is not empty. Otherwise only
-- objects whose names start with LA, CN or ST (the names this exporter
-- writes). Other geometry in the scene is left alone.
--
-- A material that already has a diffuse bitmap is left unchanged, so an
-- existing texture is not replaced. The diffuse color is copied onto the
-- new Standard material.
--
-- This file has not been run inside 3ds Max. No Max license is available
-- in the environment that produced it.
--
-- In 3ds Max: select the imported objects, or select nothing to use the
-- name filter, then Scripting > Run Script and choose this file.

fn tankExtractHasDiffuseMap mat = (
    if mat == undefined then return false
    if (isProperty mat #diffuseMap) and mat.diffuseMap != undefined then return true
    false
)

fn tankExtractInScope obj = (
    if selection.count > 0 then return (findItem selection obj) > 0
    local n = obj.name
    (matchPattern n pattern:"LA*") or (matchPattern n pattern:"CN*") or (matchPattern n pattern:"ST*")
)

fn tankExtractAssignStandard = (
    local count = 0
    local skipped = 0
    for obj in objects where (isKindOf obj GeometryClass) and (tankExtractInScope obj) do (
        local old = obj.material
        if old == undefined then (
            skipped += 1
            continue
        )
        if tankExtractHasDiffuseMap old then (
            skipped += 1
            continue
        )
        local stdMat = Standardmaterial name:old.name
        if isProperty old #diffuse then stdMat.diffuse = old.diffuse
        else stdMat.diffuse = color 220 220 220
        obj.material = stdMat
        count += 1
    )
    format "TankExtract: Standard assigned to % objects, % left unchanged. Not run in 3ds Max by the extractor.\n" count skipped
    count
)

tankExtractAssignStandard()
