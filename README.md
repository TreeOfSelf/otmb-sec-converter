# otmb-sec-converter
Convert Open Tibia Map Binary to and from Official CipSoft Sector Map files


## Notes

### Having to offset spawn center coordinates
`duplicate spawn on position 32418 32157 15`
`duplicate spawn non position 32666 31676 15`

### Stone wall for spawn sprite
`1507 stone wall hardcoded into RME`

### Omitted RemainingExpireTime on pools/SavedExpireTime on torches
`No sensible field & server gives default`

### Omitted RemainingUses on ice rapier (3284)
`No sensible field & server gives default`

Prefer no wall when shifting spawns

Liquids seem wrong sometimes 

parse moveuse.dat (whats the different between those and absteleportdestination ?)